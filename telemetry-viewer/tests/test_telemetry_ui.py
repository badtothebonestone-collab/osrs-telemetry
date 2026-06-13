import json
import inspect
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import manual_recorder
import start_game_command
import telemetry_ui


class TelemetryUiTest(unittest.TestCase):
    def test_config_load_save_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telemetry_ui_config.json"
            config = telemetry_ui.default_config()
            config["last_recording_label"] = "bank_test"
            config["last_recording_description"] = "walk from lower stairs to trees"
            config["context_service_port"] = "8899"
            telemetry_ui.save_config(config, path)

            loaded = telemetry_ui.load_config(path)
            self.assertEqual(loaded["schema_version"], telemetry_ui.CONFIG_SCHEMA)
            self.assertEqual(loaded["last_recording_label"], "bank_test")
            self.assertEqual(loaded["last_recording_description"], "walk from lower stairs to trees")
            self.assertEqual(loaded["context_service_port"], "8899")
            self.assertEqual(loaded["input_backend"], "polling")
            self.assertTrue(loaded["prefer_polling_input"])
            self.assertTrue(loaded["input_preflight"])
            self.assertEqual(loaded["route_name"], "Bank_to_Woodcutting_area")
            self.assertEqual(loaded["route_template_revision"], 3)

    def test_recorder_and_analyzer_command_construction(self):
        config = telemetry_ui.default_config()
        config.update(
            {
                "last_recording_label": "hover tree",
                "last_recording_description": "hover the target tree before clicking",
                "duration": "0",
                "poll_interval_ms": "25",
                "latest_session": True,
                "include_raw": True,
                "pretty": True,
                "sources_override": "status=C:\\tmp\\status.json",
                "capture_input": True,
                "input_backend": "polling",
                "prefer_polling_input": True,
                "input_preflight": True,
                "raw_input_device_attribution": True,
                "arduino_passthrough_mode": "mirror",
                "arduino_probe": True,
                "arduino_probe_move_dx": "12",
                "arduino_probe_move_dy": "0",
                "require_arduino_probe_verified": True,
                "arduino_mirror_preflight": True,
                "arduino_live_mirror": True,
                "require_live_mirror_active": True,
                "mirror_move_min_px": "1",
                "mirror_max_step_px": "25",
                "mirror_send_interval_ms": "5",
                "mirror_button_mode": "click",
                "input_path_integrity": True,
            }
        )
        command = telemetry_ui.build_recorder_command(
            config,
            stop_file=Path("C:/tmp/stop.flag"),
            marker_file=Path("C:/tmp/markers.txt"),
        )
        self.assertIn("--until-stopped", command)
        self.assertIn("--stop-file", command)
        self.assertIn("--marker-file", command)
        self.assertIn("--description", command)
        self.assertIn("hover the target tree before clicking", command)
        self.assertIn("--sources", command)
        self.assertIn("--include-raw", command)
        self.assertIn("--pretty", command)
        self.assertIn("--input-backend", command)
        self.assertIn("polling", command)
        self.assertIn("--prefer-polling-input", command)
        self.assertIn("--input-preflight", command)
        self.assertIn("--raw-input-device-attribution", command)
        self.assertIn("--arduino-probe", command)
        self.assertIn("--arduino-probe-move", command)
        self.assertIn("--require-arduino-probe-verified", command)
        self.assertIn("--arduino-mirror-preflight", command)
        self.assertIn("--arduino-live-mirror", command)
        self.assertIn("--mirror-arm-mode", command)
        self.assertIn("recording_persistent", command)
        self.assertIn("--mirror-persist-until-stop", command)
        self.assertIn("--mirror-keep-armed-while-recording", command)
        self.assertNotIn("--mirror-test-duration-sec", command)
        self.assertIn("--mirror-click-policy", command)
        self.assertIn("--mirror-move-min-px", command)
        self.assertIn("--require-live-mirror-active", command)
        self.assertIn("--input-path-integrity", command)

        analyzer = telemetry_ui.build_analyzer_command(Path("recordings/test"))
        self.assertIn("telemetry-viewer\\analyze_manual_recording.py", analyzer)
        self.assertIn("--summary", analyzer)
        self.assertIn("--schema-gap", analyzer)
        self.assertIn("--banking-lifecycle", analyzer)
        self.assertIn("--combat-damage-summary", analyzer)
        self.assertIn("--woodcutting-loop-lifecycle", analyzer)
        self.assertIn("--target-match-quality", analyzer)
        self.assertIn("--menu-interactions", analyzer)
        self.assertIn("--coordinate-alignment", analyzer)
        self.assertIn("--input-path-integrity", analyzer)
        self.assertIn("--traversal-lifecycle", analyzer)
        self.assertIn("--update-knowledge", analyzer)

        smoke = telemetry_ui.build_input_smoke_test_command(config, out="C:\\tmp\\smoke")
        self.assertIn("--smoke-test", smoke)
        self.assertIn("--backend", smoke)
        self.assertIn("polling", smoke)

        probe = telemetry_ui.build_arduino_probe_command(config, out="C:\\tmp\\probe")
        self.assertIn("telemetry-viewer\\arduino_mirror_verifier.py", probe)
        self.assertIn("--probe", probe)
        self.assertIn("--move", probe)
        self.assertIn("12", probe)
        live_test = telemetry_ui.build_live_mirror_test_command(config, out="C:\\tmp\\live_mirror")
        self.assertIn("--arduino-live-mirror", live_test)
        self.assertIn("--mirror-button-mode", live_test)
        self.assertIn("--mirror-arm-mode", live_test)
        self.assertIn("test_window", live_test)
        self.assertIn("--mirror-test-duration-sec", live_test)

    def test_preset_command_construction(self):
        basic = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_BASIC)
        self.assertEqual(basic["last_recording_label"], "manual_recording")
        basic_command = telemetry_ui.build_recorder_command(
            basic,
            stop_file=Path("C:/tmp/basic.stop"),
            marker_file=Path("C:/tmp/basic.markers"),
        )
        self.assertNotIn("--arduino-live-mirror", basic_command)

        menu = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_MENU_ROW)
        self.assertEqual(menu["last_recording_label"], "manual_action-menu_row_validation")
        menu_command = telemetry_ui.build_recorder_command(
            menu,
            stop_file=Path("C:/tmp/menu.stop"),
            marker_file=Path("C:/tmp/menu.markers"),
        )
        self.assertIn("--capture-input", menu_command)
        self.assertIn("--join-input-telemetry", menu_command)
        self.assertIn("--input-path-integrity", menu_command)
        self.assertNotIn("--arduino-live-mirror", menu_command)

        mirror = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_LIVE_MIRROR_MENU_ROW)
        mirror_command = telemetry_ui.build_recorder_command(
            mirror,
            stop_file=Path("C:/tmp/mirror.stop"),
            marker_file=Path("C:/tmp/mirror.markers"),
        )
        self.assertIn("--arduino-live-mirror", mirror_command)
        self.assertIn("--mirror-arm-mode", mirror_command)
        self.assertIn("recording_persistent", mirror_command)
        self.assertIn("--mirror-persist-until-stop", mirror_command)
        self.assertIn("--mirror-profile", mirror_command)
        self.assertIn("validation_menu_row", mirror_command)
        self.assertIn("--mirror-click-policy", mirror_command)
        click_policy_index = mirror_command.index("--mirror-click-policy")
        self.assertEqual(mirror_command[click_policy_index + 1], "map_only")
        self.assertIn("--mirror-disable-movement", mirror_command)
        self.assertIn("--mirror-echo-suppression", mirror_command)
        self.assertIn("--mirror-clear-queue-on-menu-selection", mirror_command)
        self.assertIn("--mirror-auto-pause-after-menu-selection", mirror_command)
        self.assertNotIn("--mirror-test-duration-sec", mirror_command)
        arm_delay_index = mirror_command.index("--mirror-arm-delay-ms")
        self.assertEqual(mirror_command[arm_delay_index + 1], "500")

        route = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_ROUTE)
        self.assertEqual(route["last_recording_label"], "manual_route-bank_to_woodcutting_area")
        route_analyzer = telemetry_ui.build_analyzer_command(Path("recordings/route"), route)
        self.assertIn("--traversal-lifecycle", route_analyzer)
        self.assertIn("--compare-route-template", route_analyzer)
        self.assertIn("--route-monitor", route_analyzer)
        self.assertIn("--route-history", route_analyzer)
        self.assertTrue(route["menu_capture_burst"])
        self.assertTrue(route["menu_burst_until_selection"])
        self.assertTrue(route["route_monitor_enabled"])
        self.assertTrue(route["route_history_enabled"])
        self.assertFalse(route["arduino_enabled"])
        self.assertFalse(route["arduino_live_mirror"])
        route_command = telemetry_ui.build_recorder_command(
            route,
            stop_file=Path("C:/tmp/route.stop"),
            marker_file=Path("C:/tmp/route.markers"),
        )
        self.assertNotIn("--arduino-live-mirror", route_command)
        self.assertNotIn("--arduino-probe", route_command)

    def test_preset_labels_preserve_custom_labels(self):
        config = telemetry_ui.default_config()
        config["last_recording_label"] = "my_custom_route_take"
        config["recording_label_mode"] = "custom"

        route = telemetry_ui.config_for_preset(config, telemetry_ui.PRESET_ROUTE)
        self.assertEqual(route["last_recording_label"], "my_custom_route_take")
        self.assertEqual(route["recording_label_mode"], "custom")

    def test_preset_change_updates_auto_generated_label_only(self):
        config = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_MENU_ROW)
        self.assertEqual(config["last_recording_label"], "manual_action-menu_row_validation")

        route = telemetry_ui.config_for_preset(config, telemetry_ui.PRESET_ROUTE)
        self.assertEqual(route["last_recording_label"], "manual_route-bank_to_woodcutting_area")
        self.assertEqual(route["recording_label_mode"], "auto")

        custom = dict(config)
        custom["last_recording_label"] = "keep_this_label"
        custom["recording_label_mode"] = "custom"
        route_custom = telemetry_ui.config_for_preset(custom, telemetry_ui.PRESET_ROUTE)
        self.assertEqual(route_custom["last_recording_label"], "keep_this_label")

    def test_command_preview_includes_run_game(self):
        preview = telemetry_ui.build_command_preview(telemetry_ui.default_config())
        self.assertIn("Run Game:", preview)
        self.assertIn("Dev Start Command:", preview)
        self.assertIn("Live Start Command:", preview)
        self.assertIn("Start Telemetry Stack:", preview)
        self.assertIn("Check Route Readiness:", preview)
        self.assertIn("Monitor Latest Route Recording:", preview)
        self.assertIn("Start Route Monitor:", preview)
        self.assertIn("Bootstrap Check:", preview)
        self.assertIn("Command Registry Check:", preview)
        self.assertIn("Bot Eval Preflight:", preview)
        self.assertIn("Input Geometry Check:", preview)
        self.assertIn("Refresh Project Knowledge:", preview)
        self.assertIn("Generate Human Click Profile:", preview)

    def test_start_game_command_helper_is_shared_with_recovery(self):
        ui_command = telemetry_ui.command_text(telemetry_ui.discover_game_launch_command())
        resolved = start_game_command.resolve_start_game_command(config_path=Path("C:/definitely_missing_ui_config.json"))
        start_game_source = inspect.getsource(telemetry_ui.TelemetryControlApp.start_game)

        self.assertEqual(resolved["status"], "PASS")
        self.assertEqual(ui_command, resolved["command"])
        self.assertEqual(resolved["launchMode"], "dev_gradle_run")
        self.assertIn(resolved["commandSource"], {"discovered_gradle_wrapper", "ui_config:C:\\definitely_missing_ui_config.json"})
        self.assertIn("start_game_command.resolve_start_game_command", start_game_source)

    def test_check_payload_reports_game_launch_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = telemetry_ui.check_payload(config_path=Path(tmp) / "ui_config.json")

        self.assertEqual(payload["game_launch"]["launchMode"], "dev_gradle_run")
        self.assertTrue(payload["game_launch"]["launchModeWarnings"])
        self.assertIn("devStartCommand", payload["game_launch"])
        self.assertIn("liveStartCommand", payload["game_launch"])
        self.assertIn("liveResolutionStatus", payload["game_launch"])
        self.assertIn("liveLaunchMode", payload["game_launch"])
        self.assertIn("authenticated_game_start", payload["commands"])
        self.assertIn("dev_start", payload["commands"])
        self.assertIn("live_start", payload["commands"])
        self.assertIn("bootstrap_check", payload["commands"])
        self.assertIn("command_registry.py", payload["commands"]["command_registry_check"])
        self.assertIn("bot_eval_runner.py", payload["commands"]["bot_eval_preflight"])
        self.assertIn("--check-input-geometry", payload["commands"]["input_geometry_check"])

    def test_simple_mode_screen_model_has_core_buttons_only(self):
        model = telemetry_ui.simple_screen_model(telemetry_ui.default_config())
        self.assertEqual(model["title"], "OSRS Telemetry Recorder")
        self.assertEqual(model["mode"], telemetry_ui.UI_MODE_SIMPLE)
        self.assertEqual(
            model["mainButtons"],
            [
                "Start Game",
                "Start Telemetry",
                "Start Recording",
                "Stop Recording",
                "Analyze Latest",
                "Open Output Folder",
                "Diagnostics / Settings",
            ],
        )
        self.assertTrue(model["advancedHiddenByDefault"])
        self.assertFalse(model["advancedTabsVisible"])
        self.assertTrue(model["diagnosticsSeparateWindow"])
        self.assertEqual(model["defaultProfile"], telemetry_ui.PROFILE_RECORD_EVERYTHING)
        self.assertIn("profile dropdown", model["hiddenControls"])
        self.assertIn("route template path", model["hiddenControls"])
        self.assertIn("Arduino mirror controls", model["hiddenControls"])

    def test_universal_human_recording_profile_builds_safe_recorder_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = telemetry_ui.config_for_recording_profile(telemetry_ui.default_config(), telemetry_ui.PROFILE_UNIVERSAL_HUMAN)
            config["output_folder"] = str(Path(tmp) / "recordings")
            command = telemetry_ui.build_recorder_command(
                config,
                stop_file=Path(tmp) / "stop.flag",
                marker_file=Path(tmp) / "markers.txt",
            )
        self.assertEqual(command[command.index("--label") + 1].split("_")[0], "manual")
        self.assertIn("--out-dir", command)
        self.assertIn("--telemetry-preflight", command)
        self.assertIn("--wait-for-fresh-telemetry", command)
        self.assertIn("--latest-session", command)
        self.assertIn("--prefer-active-session", command)
        self.assertIn("--capture-input", command)
        self.assertIn("--input-backend", command)
        self.assertIn("polling", command)
        self.assertIn("--prefer-polling-input", command)
        self.assertIn("--capture-mouse", command)
        self.assertIn("--capture-keyboard", command)
        self.assertIn("--raw-input-device-attribution", command)
        self.assertIn("--capture-window-context", command)
        self.assertIn("--join-input-telemetry", command)
        self.assertIn("--camera-behavior", command)
        self.assertIn("--menu-capture-burst", command)
        self.assertIn("--menu-burst-until-selection", command)
        self.assertIn("--preserve-bank-ui", command)
        self.assertIn("--plugin-snapshot-url", command)
        self.assertNotIn("--arduino-live-mirror", command)
        self.assertNotIn("--arduino", command)
        self.assertFalse(config["arduino_required_for_recording"])
        self.assertFalse(config.get("require_live_mirror_active"))

    def test_record_everything_profile_exists_and_does_not_require_route_template(self):
        config = telemetry_ui.default_config()
        config["route_template_path"] = "missing_template_for_generic_recording"
        record_everything = telemetry_ui.config_for_recording_profile(config, telemetry_ui.PROFILE_RECORD_EVERYTHING)
        self.assertEqual(record_everything["recording_profile"], telemetry_ui.PROFILE_RECORD_EVERYTHING)
        self.assertFalse(record_everything["arduino_required_for_recording"])
        self.assertFalse(record_everything["route_template_required"])

    def test_universal_profile_uses_mapping_only_when_arduino_enabled(self):
        config = telemetry_ui.default_config()
        config["arduino_enabled"] = True
        config["arduino_port"] = "COM_TEST"
        universal = telemetry_ui.config_for_recording_profile(config, telemetry_ui.PROFILE_UNIVERSAL_HUMAN)
        self.assertEqual(universal["mirror_click_policy"], "map_only")
        self.assertTrue(universal["mirror_disable_movement"])
        self.assertTrue(universal["mirror_disable_clicks"])
        command = telemetry_ui.build_recorder_command(
            universal,
            stop_file=Path("C:/tmp/universal.stop"),
            marker_file=Path("C:/tmp/universal.markers"),
        )
        self.assertIn("--arduino", command)
        self.assertIn("--arduino-port", command)
        self.assertIn("--arduino-passthrough-mode", command)
        self.assertIn("label_only", command)
        self.assertIn("--vm-mouse-mapping", command)
        self.assertNotIn("--arduino-live-mirror", command)

    def test_universal_profile_respects_output_folder_and_generated_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = telemetry_ui.default_config()
            config["output_folder"] = str(Path(tmp) / "human_recordings")
            universal = telemetry_ui.config_for_recording_profile(config, telemetry_ui.PROFILE_UNIVERSAL_HUMAN)
            command = telemetry_ui.build_recorder_command(
                universal,
                stop_file=Path(tmp) / "stop.flag",
                marker_file=Path(tmp) / "markers.txt",
            )
        self.assertTrue(str(universal["last_recording_label"]).startswith("manual_recording_"))
        self.assertEqual(command[command.index("--out-dir") + 1], str(Path(tmp) / "human_recordings"))

    def test_universal_profile_preserves_custom_label(self):
        config = telemetry_ui.default_config()
        config["last_recording_label"] = "my_human_test"
        config["recording_label_mode"] = "custom"
        universal = telemetry_ui.config_for_recording_profile(config, telemetry_ui.PROFILE_UNIVERSAL_HUMAN)
        self.assertEqual(universal["last_recording_label"], "my_human_test")

    def test_analyzer_command_for_simple_mode_has_broad_flags(self):
        config = telemetry_ui.config_for_recording_profile(telemetry_ui.default_config(), telemetry_ui.PROFILE_UNIVERSAL_HUMAN)
        analyzer = telemetry_ui.build_analyzer_command(Path("recordings/test"), config)
        self.assertIn("--summary", analyzer)
        self.assertIn("--schema-gap", analyzer)
        self.assertIn("--banking-lifecycle", analyzer)
        self.assertIn("--input-trace", analyzer)
        self.assertIn("--join-input", analyzer)
        self.assertIn("--camera-behavior", analyzer)
        self.assertIn("--human-click-profile", analyzer)
        self.assertIn("--classify-input-actions", analyzer)
        self.assertIn("--target-match-quality", analyzer)
        self.assertIn("--menu-interactions", analyzer)
        self.assertIn("--coordinate-alignment", analyzer)
        self.assertIn("--input-path-integrity", analyzer)
        self.assertIn("--traversal-lifecycle", analyzer)
        self.assertIn("--auto-route-template", analyzer)
        self.assertIn("--route-history", analyzer)

    def test_analysis_run_applies_simple_profile_defaults(self):
        raw = telemetry_ui.default_config()
        raw["recording_profile"] = telemetry_ui.PROFILE_UNIVERSAL_HUMAN
        raw["route_monitor_enabled"] = False
        raw["route_history_enabled"] = False

        config = telemetry_ui.config_for_analysis_run(raw)
        analyzer = telemetry_ui.build_analyzer_command(Path("recordings/test"), config)

        self.assertTrue(config["route_monitor_enabled"])
        self.assertTrue(config["route_history_enabled"])
        self.assertIn("--auto-route-template", analyzer)
        self.assertNotIn("--compare-route-template", analyzer)
        self.assertIn("--route-monitor", analyzer)
        self.assertIn("--route-history", analyzer)

    def test_analyze_latest_uses_background_process_manager(self):
        source = inspect.getsource(telemetry_ui.TelemetryControlApp.analyze_latest_recording)
        self.assertIn('start_process("analyzer"', source)
        self.assertNotIn(".communicate(", source)

    def test_analysis_progress_text_and_timeout(self):
        self.assertEqual(telemetry_ui.analysis_progress_text("analyzing", elapsed_seconds=15.9), "Analyzing... 15s")
        self.assertFalse(telemetry_ui.analyzer_timed_out(10.0, 20.0, 30))
        self.assertTrue(telemetry_ui.analyzer_timed_out(10.0, 41.0, 30))

    def test_completed_analysis_result_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp)
            (recording / "schema_gap_report.md").write_text("# report\n", encoding="utf-8")
            (recording / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "traversal_lifecycle": {"status": "PASS", "routeName": "Bank_to_Woodcutting_area", "routeSegmentCount": 5},
                        "warnings": ["largest warning"],
                    }
                ),
                encoding="utf-8",
            )
            result = telemetry_ui.safe_analysis_result(recording)
        self.assertTrue(result["summaryPresent"])
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["detectedActivityType"], "Route / Traversal")
        self.assertEqual(result["biggestWarning"], "largest warning")
        self.assertTrue(str(result["reportPath"]).endswith("schema_gap_report.md"))

    def test_detected_activity_prefers_named_route_over_bank_label(self):
        activity = telemetry_ui.detected_activity_type(
            {
                "label": "Bank to tree area",
                "traversal_lifecycle": {
                    "status": "PASS",
                    "routeName": "Bank_to_Woodcutting_area",
                    "routeSegmentCount": 5,
                    "start": {"areaLabel": "bank_area"},
                    "end": {"areaLabel": "woodcutting_area"},
                },
                "input_action_summary": {},
            }
        )
        self.assertEqual(activity, "Route / Traversal")

    def test_woodcutting_loop_simple_summary_lists_both_route_legs(self):
        lifecycle = {
            "loopState": "complete",
            "detectedPhases": [
                {"phase": "cutting", "status": "PASS"},
                {"phase": "routing_to_bank", "status": "PASS"},
                {"phase": "banking", "status": "PASS"},
                {"phase": "routing_to_trees", "status": "PASS"},
                {"phase": "resumed_cutting", "status": "PASS"},
            ],
            "routes": {
                "routeLegs": [
                    {"phase": "route_to_bank", "routeName": "woodcutting_area_to_bank", "direction": "woodcutting_area_to_bank"},
                    {"phase": "route_to_trees", "routeName": "Bank_to_Woodcutting_area", "direction": "bank_to_woodcutting_area"},
                ]
            },
        }
        text = telemetry_ui.woodcutting_loop_simple_phase_summary(lifecycle)
        self.assertIn("Woodcutting: PASS", text)
        self.assertIn("Route to Bank: PASS, woodcutting_area_to_bank", text)
        self.assertIn("Banking: PASS", text)
        self.assertIn("Route to Trees: PASS, Bank_to_Woodcutting_area", text)
        self.assertIn("Resume Cutting: PASS", text)

    def test_detected_activity_labels_deposit_sample_as_banking(self):
        activity = telemetry_ui.detected_activity_type(
            {
                "label": "Opening Bank and Deposit all logs",
                "traversal_lifecycle": {
                    "status": "WARN",
                    "routeName": "route_unknown",
                    "routeSegmentCount": 2,
                    "start": {"areaLabel": "bank_area"},
                    "end": {"areaLabel": "bank_area"},
                },
                "input_action_summary": {},
            }
        )
        self.assertEqual(activity, "Banking")

    def test_detected_activity_uses_banking_lifecycle(self):
        activity = telemetry_ui.detected_activity_type(
            {
                "label": "manual_recording",
                "banking_lifecycle": {
                    "status": "WARN",
                    "deposit": {"detected": True, "items": [{"name": "Logs", "quantity": 6}]},
                    "bank": {"openSeen": False, "targetEvidence": [{"name": "Bank booth"}]},
                },
            }
        )
        self.assertEqual(activity, "Banking")

    def test_detected_activity_labels_log_gain_as_woodcutting(self):
        activity = telemetry_ui.detected_activity_type(
            {
                "label": "manual_recording",
                "traversal_lifecycle": {
                    "status": "PASS",
                    "routeName": "route_unknown",
                    "routeSegmentCount": 3,
                    "start": {"areaLabel": "woodcutting_area"},
                    "end": {"areaLabel": "woodcutting_area"},
                },
                "woodcutting_lifecycle": {
                    "status": "WARN",
                    "phase": "log_gained",
                    "inventory": {"normalLogsGained": 1},
                },
            }
        )
        self.assertEqual(activity, "Woodcutting")

    def test_missing_analysis_summary_is_warn_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = telemetry_ui.safe_analysis_result(Path(tmp))
        self.assertFalse(result["summaryPresent"])
        self.assertEqual(result["verdict"], "WARN")
        self.assertIn("summary.json", result["biggestWarning"])

    def test_ui_recording_manifest_is_mirrored_into_recording_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_manifest = root / "ui_control" / "ui_recording_session_manifest.json"
            recording = root / "recordings" / "sample"
            recording.mkdir(parents=True)

            telemetry_ui.write_ui_recording_manifest(
                {
                    "schema": telemetry_ui.UI_RECORDING_SESSION_SCHEMA,
                    "profile": telemetry_ui.PROFILE_RECORD_EVERYTHING,
                    "recordingFolder": None,
                },
                control_manifest,
            )
            telemetry_ui.update_ui_recording_manifest(
                {
                    "recordingFolder": str(recording),
                    "finalVerdict": "PASS",
                    "detectedActivityType": "Route / Traversal",
                },
                control_manifest,
            )

            mirrored = recording / "ui_recording_session_manifest.json"
            self.assertTrue(mirrored.exists())
            payload = json.loads(mirrored.read_text(encoding="utf-8"))
            self.assertEqual(payload["profile"], telemetry_ui.PROFILE_RECORD_EVERYTHING)
            self.assertEqual(payload["recordingFolder"], str(recording))
            self.assertEqual(payload["finalVerdict"], "PASS")

    def test_check_payload_validates_simple_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = telemetry_ui.check_payload(config_path=Path(tmp) / "telemetry_ui_config.json")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["simple_screen"]["title"], "OSRS Telemetry Recorder")
        self.assertEqual(payload["record_everything_profile"]["profile"], telemetry_ui.PROFILE_RECORD_EVERYTHING)
        self.assertTrue(str(payload["record_everything_profile"]["recordingLabel"]).startswith("manual_recording_"))
        self.assertFalse(payload["record_everything_profile"]["requiresArduino"])
        self.assertFalse(payload["record_everything_profile"]["requiresRouteTemplate"])

    def test_broken_config_auto_repairs_simple_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telemetry_ui_config.json"
            path.write_text(json.dumps({"ui_mode": "advanced", "advanced_expanded": True, "recording_profile": "bad", "selected_preset": telemetry_ui.PRESET_ROUTE, "output_folder": ""}), encoding="utf-8")
            loaded = telemetry_ui.load_config(path)
        self.assertEqual(loaded["ui_mode"], telemetry_ui.UI_MODE_SIMPLE)
        self.assertFalse(loaded["advanced_expanded"])
        self.assertEqual(loaded["recording_profile"], telemetry_ui.PROFILE_RECORD_EVERYTHING)
        self.assertEqual(loaded["selected_preset"], telemetry_ui.PRESET_BASIC)
        self.assertTrue(Path(loaded["output_folder"]).is_absolute())

    def test_route_monitor_command_construction(self):
        config = telemetry_ui.default_config()
        config["route_template_path"] = "Bank_to_Woodcutting_area.route_template.json"
        readiness = telemetry_ui.build_route_readiness_command(config)
        self.assertIsNotNone(readiness)
        self.assertIn("telemetry-viewer\\route_monitor.py", readiness)
        self.assertIn("--template", readiness)
        template_index = readiness.index("--template")
        self.assertEqual(readiness[template_index + 1], "auto")
        self.assertIn("--live", readiness)
        self.assertIn("--latest-session", readiness)
        self.assertIn("--json", readiness)

        monitor = telemetry_ui.build_route_monitor_recording_command(Path("recordings/latest"), config)
        self.assertIsNotNone(monitor)
        self.assertIn("--recording", monitor)
        self.assertIn("recordings\\latest", " ".join(str(item) for item in monitor).replace("/", "\\"))

        follow = telemetry_ui.build_route_history_follow_command(config)
        self.assertIsNotNone(follow)
        self.assertIn("--follow", follow)
        self.assertIn("--poll-ms", follow)
        self.assertIn("--out-dir", follow)
        self.assertEqual(follow[follow.index("--template") + 1], "auto")

    def test_route_session_plan_uses_resolved_template_path(self):
        config = telemetry_ui.config_for_preset(telemetry_ui.default_config(), telemetry_ui.PRESET_ROUTE)
        config["route_template_path"] = "Bank_to_Woodcutting_area"
        plan = telemetry_ui.build_route_session_plan("Bank_to_Woodcutting_area", config)
        self.assertTrue(plan["canStart"])
        self.assertEqual(plan["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(plan["templateRevision"], 3)
        self.assertEqual(plan["presetName"], telemetry_ui.PRESET_ROUTE)
        self.assertEqual(plan["recordingLabel"], "manual_route-bank_to_woodcutting_area")
        self.assertTrue(Path(plan["templatePath"]).is_absolute())
        monitor = plan["routeMonitorCommand"]
        self.assertIn("--template", monitor)
        self.assertEqual(monitor[monitor.index("--template") + 1], plan["templatePath"])
        self.assertIn("--session-id", monitor)
        self.assertEqual(monitor[monitor.index("--session-id") + 1], plan["sessionId"])
        self.assertIn("Bank_to_Woodcutting_area", plan["routeMonitorSessionFolder"])
        self.assertTrue(plan["routeMonitorSessionFolder"].endswith(plan["sessionId"]))
        recorder = plan["recorderCommand"]
        label_index = recorder.index("--label")
        self.assertEqual(recorder[label_index + 1], "manual_route-bank_to_woodcutting_area")

    def test_route_session_plan_replaces_stale_auto_label(self):
        config = telemetry_ui.default_config()
        config["last_recording_label"] = "manual_action-menu_row_validation_live_mirror_controlled"
        config.pop("recording_label_mode", None)

        plan = telemetry_ui.build_route_session_plan("Bank_to_Woodcutting_area", config)
        self.assertTrue(plan["canStart"])
        self.assertEqual(plan["recordingLabel"], "manual_route-bank_to_woodcutting_area")
        recorder = plan["recorderCommand"]
        self.assertEqual(recorder[recorder.index("--label") + 1], "manual_route-bank_to_woodcutting_area")

    def test_bad_saved_template_config_migrates_to_default(self):
        config = telemetry_ui.merge_config({"route_template_path": "Bank_to_Woodcutting_area.route_template.json"})
        self.assertTrue(Path(config["route_template_path"]).is_absolute())
        self.assertEqual(config["route_name"], "Bank_to_Woodcutting_area")
        self.assertEqual(config["route_template_revision"], 3)

    def test_route_session_plan_blocks_explicit_invalid_template(self):
        plan = telemetry_ui.build_route_session_plan("missing_route_template_for_unit_test", telemetry_ui.default_config())
        self.assertFalse(plan["canStart"])
        self.assertEqual(plan["templateResolution"]["status"], "FAIL")
        self.assertIsNone(plan["routeMonitorCommand"])
        self.assertIsNone(plan["recorderCommand"])

    def test_check_payload_validates_default_route_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = telemetry_ui.check_payload(config_path=Path(tmp) / "telemetry_ui_config.json")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["route_template_resolution"]["status"], "PASS")
        self.assertEqual(payload["route_template_resolution"]["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(payload["route_template_resolution"]["templateRevision"], 3)
        self.assertTrue(payload["route_session_plan"]["canStart"])
        self.assertEqual(payload["route_session_plan"]["requiredSegmentCount"], 5)
        self.assertTrue(payload["knowledge_update"]["enabledByDefault"])
        self.assertTrue(payload["knowledge_update"]["scriptExists"])
        self.assertIn("update_project_knowledge.py", payload["knowledge_update"]["command"])

    def test_latest_recording_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "recordings" / "20260602_100000_first"
            second = root / "recordings" / "20260602_110000_second"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            first_time = 1_800_000_000
            second_time = first_time + 10
            first.touch()
            second.touch()
            import os

            os.utime(first, (first_time, first_time))
            os.utime(second, (second_time, second_time))
            self.assertEqual(telemetry_ui.latest_recording_dir(root), second)

    def test_stop_file_and_marker_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = Path(tmp) / "record.stop"
            marker = Path(tmp) / "record.markers"
            telemetry_ui.ensure_stop_file(stop)
            telemetry_ui.append_marker_line(marker, "clicked bank")
            self.assertTrue(stop.exists())
            self.assertIn("clicked bank", marker.read_text(encoding="utf-8"))

    def test_status_parsing_with_synthetic_live_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session1"
            live = session / "interaction_geometry" / "live"
            live.mkdir(parents=True)
            (live / "live_status.json").write_text(
                json.dumps({"latestTickProcessed": 7, "compactPacketLastSequence": 42}),
                encoding="utf-8",
            )
            (live / "live_baseline_state.json").write_text(
                json.dumps({"gameState": "LOGGED_IN", "player": {"worldX": 3200, "worldY": 3201, "plane": 0}}),
                encoding="utf-8",
            )
            config = telemetry_ui.default_config()
            config["latest_session"] = False
            config["last_session_path"] = str(session)

            status = telemetry_ui.file_status_snapshot(config)
            self.assertEqual(status["session_path"], str(session.resolve()))
            self.assertEqual(status["latest_tick"], 7)
            self.assertEqual(status["latest_export_sequence"], 42)
            self.assertGreaterEqual(status["source_freshness"]["present_count"], 2)

    def test_manual_recorder_until_stopped_with_stop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_file = root / "stop.flag"
            stop_file.write_text("stop\n", encoding="utf-8")
            exit_code = manual_recorder.main(
                [
                    "--label",
                    "unit_stop",
                    "--description",
                    "unit test stop-file recording",
                    "--out-dir",
                    str(root / "recordings"),
                    "--until-stopped",
                    "--stop-file",
                    str(stop_file),
                    "--poll-interval-ms",
                    "1",
                ]
            )
            self.assertEqual(exit_code, 0)
            recording = telemetry_ui.latest_recording_dir(root)
            self.assertIsNotNone(recording)
            self.assertTrue((recording / "summary.json").exists())
            manifest = json.loads((recording / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["description"], "unit test stop-file recording")
            events = (recording / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"recording_stop"', events)
            self.assertIn('"stop_file"', events)


if __name__ == "__main__":
    unittest.main()
