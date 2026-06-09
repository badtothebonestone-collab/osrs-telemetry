import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import context_service
import route_monitor
import route_template
import traversal_lifecycle


def world(x, y, plane=0):
    return {"worldX": x, "worldY": y, "plane": plane}


def segment(index, segment_type, label, *, option=None, target=None, post="movement", result="success", start=None, end=None):
    start = start or world(3200 + index, 3200 + index, 0)
    end = end or world(3201 + index, 3201 + index, 0)
    return {
        "segmentIndex": index,
        "segmentType": segment_type,
        "label": label,
        "startWorld": start,
        "endWorld": end,
        "startPlane": start.get("plane"),
        "endPlane": end.get("plane"),
        "primaryAction": {"option": option, "target": target, "targetQuality": "strong" if target else None},
        "postcondition": {"type": post, "result": result},
        "confidence": 0.9,
        "warnings": [],
    }


def lifecycle(*, end_area="woodcutting_area", route_segments=None):
    segments = route_segments or [
        segment(1, "area_start", "Start: bank_area", post="area_start", start=world(3208, 3220, 2), end=world(3208, 3220, 2)),
        segment(2, "walk_segment", "Walk", option="Walk", start=world(3208, 3220, 2), end=world(3205, 3209, 2)),
        segment(3, "stair_transition", "Climb-down Staircase", option="Climb-down", target="Staircase", post="plane_change", start=world(3205, 3209, 2), end=world(3206, 3208, 0)),
        segment(4, "walk_segment", "Walk", option="Walk", start=world(3206, 3208, 0), end=world(3195, 3244, 0)),
        segment(5, "area_arrival", f"Arrive: {end_area}", post="area_arrival", start=world(3195, 3244, 0), end=world(3195, 3244, 0)),
    ]
    return {
        "schema": "traversal_lifecycle.v1",
        "status": "PASS",
        "routeName": "Bank_to_Woodcutting_area",
        "recordingPath": "synthetic",
        "phase": "arrived",
        "start": {"areaLabel": "bank_area", "world": world(3208, 3220, 2), "plane": 2},
        "end": {"areaLabel": end_area, "world": world(3195, 3244, 0), "plane": 0},
        "routeSegments": segments,
        "reviewEvidence": [],
    }


def template():
    return route_template.extract_template(lifecycle(), created_at_utc="2026-06-06T00:00:00Z")


def template_with_end_cluster():
    route_tmpl = template()
    route_tmpl["templateRevision"] = 3
    route_tmpl.setdefault("end", {})["endCluster"] = {
        "world": world(3197, 3244, 0),
        "toleranceTiles": 8,
    }
    return route_tmpl


def live_context(player_world, *, objects=None, age_seconds=0.0, tick=10, export_seq=20):
    return {
        "baseline": {"latestTick": tick, "player": player_world},
        "status": {"latestTick": tick, "compactPacketLastSequence": export_seq},
        "context": {"nearby_objects": objects or []},
        "candidates": [],
        "sourceFiles": [{"name": "baseline", "exists": True, "age_seconds": age_seconds}],
        "warnings": [],
        "missingFields": [],
    }


class RouteMonitorTest(unittest.TestCase):
    def test_unresolved_template_blocks_live_monitor(self):
        status = route_monitor.monitor_live_context("missing_route_template_for_unit_test", live_context(world(3208, 3220, 2)))
        self.assertEqual(status["status"], "FAIL")
        self.assertEqual(status["routeState"], "blocked")
        self.assertIn("route_template", status["missingCapabilities"])
        self.assertIn("valid template", " ".join(status["warnings"]))

    def test_valid_template_name_loads_revision_and_required_segments(self):
        status = route_monitor.monitor_live_context("Bank_to_Woodcutting_area", live_context(world(3208, 3220, 2)))
        self.assertEqual(status["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(status["templateRevision"], 3)
        self.assertEqual(status["requiredSegmentCount"], 5)
        self.assertEqual(status["templateResolution"]["status"], "PASS")

    def test_auto_template_selects_reverse_route_at_woodcutting_area(self):
        status = route_monitor.monitor_live_context("auto", live_context(world(3195, 3243, 0)))
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["routeName"], "woodcutting_area_to_bank")
        self.assertEqual(status["routeState"], "ready_at_start")
        self.assertEqual(status["requiredSegmentCount"], 5)
        self.assertEqual(status["currentArea"], "woodcutting_area")

    def test_auto_template_does_not_guess_when_current_area_unknown(self):
        status = route_monitor.monitor_live_context("auto", live_context(world(1000, 1000, 0)))
        self.assertEqual(status["status"], "WARN")
        self.assertEqual(status["routeState"], "unknown")
        self.assertIsNone(status["templatePath"])

    def test_list_route_templates_includes_start_end_areas(self):
        templates = route_template.list_route_templates()
        reverse = [item for item in templates if item.get("routeName") == "woodcutting_area_to_bank"]
        self.assertTrue(reverse)
        self.assertEqual(reverse[0]["startArea"], "woodcutting_area")
        self.assertEqual(reverse[0]["endArea"], "bank_area")

    def test_history_output_folder_uses_route_name(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        folder = route_monitor.default_history_dir(template(), state["sessionId"], out_dir=Path("C:/tmp/route_monitor_unit"))
        self.assertIn("Bank_to_Woodcutting_area", str(folder))

    def test_persistent_session_starts_unknown(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        self.assertEqual(state["schema"], "route_session_state.v1")
        self.assertEqual(state["sessionId"], "route_unit")
        self.assertEqual(state["routeState"], "unknown")
        self.assertEqual(state["completedSegments"], [])
        self.assertEqual(state["requiredSegmentCount"], 5)

    def test_session_state_keeps_template_metadata_with_public_resolution(self):
        route_tmpl = template()
        resolution = {
            "schema": "route_template_resolution.v1",
            "input": "Bank_to_Woodcutting_area",
            "resolvedPath": "C:/repo/route_templates/Bank_to_Woodcutting_area.route_template.json",
            "exists": True,
            "routeName": "Bank_to_Woodcutting_area",
            "templateRevision": 3,
            "requiredSegmentCount": 5,
            "status": "PASS",
            "warnings": [],
            "candidatesTried": [],
        }
        state = route_monitor.create_session_state(
            route_tmpl,
            template_path=resolution["resolvedPath"],
            template_input=resolution["input"],
            template_resolution=resolution,
            session_id="route_unit",
        )
        self.assertEqual(state["routeName"], "Bank_to_Woodcutting_area")
        self.assertEqual(state["templateRevision"], 3)
        self.assertEqual(state["requiredSegmentCount"], 5)
        self.assertEqual(len(state["remainingSegments"]), 5)

    def test_persistent_bank_snapshot_ready_at_start(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        events = route_monitor.update_session_with_context(
            state,
            template(),
            live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]),
            monotonic_time=1.0,
            wall_time_utc="2026-06-06T00:00:01Z",
        )
        self.assertEqual(state["routeState"], "ready_at_start")
        self.assertEqual(state["currentArea"], "bank_area")
        self.assertEqual(len(state["completedSegments"]), 1)
        self.assertTrue(any(event["eventType"] == "segment_completed" for event in events))

    def test_leaving_bank_area_moves_in_progress(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        route_monitor.update_session_with_context(
            state,
            template(),
            live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]),
            monotonic_time=1.0,
            wall_time_utc="2026-06-06T00:00:01Z",
        )
        route_monitor.update_session_with_context(
            state,
            template(),
            live_context(world(3205, 3210, 2)),
            monotonic_time=2.0,
            wall_time_utc="2026-06-06T00:00:02Z",
        )
        self.assertEqual(state["routeState"], "in_progress")
        self.assertGreaterEqual(len(state["completedSegments"]), 2)
        self.assertEqual(state["nextExpectedSegment"]["segmentType"], "stair_transition")

    def test_plane_change_completes_stair_transition(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        route_monitor.update_session_with_context(state, template(), live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, template(), live_context(world(3205, 3210, 2)), monotonic_time=2.0)
        events = route_monitor.update_session_with_context(state, template(), live_context(world(3206, 3208, 0)), monotonic_time=3.0)
        completed_types = {item["segmentType"] for item in state["completedSegments"]}
        self.assertIn("stair_transition", completed_types)
        self.assertTrue(any(event["eventType"] == "plane_changed" for event in events))

    def test_end_area_label_after_stair_does_not_immediately_arrive(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        route_tmpl = template_with_end_cluster()
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}], tick=1), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3205, 3210, 2), tick=2), monotonic_time=2.0)
        events = route_monitor.update_session_with_context(
            state,
            route_tmpl,
            live_context(world(3206, 3208, 0), objects=[{"effectiveName": "Tree"}], tick=3),
            monotonic_time=3.0,
        )
        self.assertEqual(state["routeState"], "in_progress")
        self.assertEqual(state["arrivalGateStatus"], "waiting")
        self.assertTrue(state["prematureArrivalPrevented"])
        self.assertTrue(state["arrivalGateRequiresEndCluster"])
        self.assertFalse(state["nearEndCluster"])
        self.assertGreater(float(state["distanceToEndCluster"]), 8.0)
        self.assertNotIn("area_arrival", {item["segmentType"] for item in state["completedSegments"]})
        self.assertTrue(any(event["eventType"] == "arrival_candidate" for event in events))
        self.assertTrue(any(event["eventType"] == "arrival_gate_waiting" for event in events))
        self.assertTrue(any(event["eventType"] == "arrival_candidate_area_label_only" for event in events))

    def test_area_arrival_completes_near_end_cluster_after_second_walk(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        route_tmpl = template_with_end_cluster()
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}], tick=1), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3205, 3210, 2), tick=2), monotonic_time=2.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3206, 3208, 0), objects=[{"effectiveName": "Tree"}], tick=3), monotonic_time=3.0)
        events = route_monitor.update_session_with_context(
            state,
            route_tmpl,
            live_context(world(3197, 3244, 0), objects=[{"effectiveName": "Tree"}], tick=6),
            monotonic_time=4.0,
        )
        self.assertEqual(state["routeState"], "arrived")
        self.assertEqual(state["arrivalGateStatus"], "passed")
        self.assertTrue(state["nearEndCluster"])
        self.assertEqual(state["arrivalGatePassedReason"], "near_end_cluster")
        self.assertEqual(state["arrivalCompletedAtWorld"], {"x": 3197, "y": 3244, "plane": 0})
        self.assertIn("walk_segment", {item["segmentType"] for item in state["completedSegments"]})
        self.assertIn("area_arrival", {item["segmentType"] for item in state["completedSegments"]})
        self.assertTrue(any(event["eventType"] == "second_walk_completed" for event in events))
        self.assertTrue(any(event["eventType"] == "arrival_gate_passed" for event in events))
        self.assertTrue(any(event["eventType"] == "arrival_gate_passed_near_end_cluster" for event in events))

    def test_distance_after_transition_alone_does_not_complete_area_arrival(self):
        state = route_monitor.create_session_state(template_with_end_cluster(), session_id="route_unit")
        route_tmpl = template_with_end_cluster()
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}], tick=1), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3205, 3210, 2), tick=2), monotonic_time=2.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3206, 3208, 0), objects=[{"effectiveName": "Tree"}], tick=3), monotonic_time=3.0)
        events = route_monitor.update_session_with_context(
            state,
            route_tmpl,
            live_context(world(3202, 3218, 0), objects=[{"effectiveName": "Tree"}], tick=6),
            monotonic_time=4.0,
        )
        self.assertEqual(state["routeState"], "in_progress")
        self.assertIn("walk_segment", {item["segmentType"] for item in state["completedSegments"]})
        self.assertNotIn("area_arrival", {item["segmentType"] for item in state["completedSegments"]})
        self.assertTrue(state["distanceOnlyProgressRejected"])
        self.assertEqual(state["arrivalGateRejectedReason"], "distance_only_progress_not_arrival")
        self.assertTrue(any(event["eventType"] == "arrival_gate_rejected_distance_only" for event in events))

    def test_arrived_event_emitted_once_after_arrival(self):
        state = route_monitor.create_session_state(template_with_end_cluster(), session_id="route_unit")
        route_tmpl = template_with_end_cluster()
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}], tick=1), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3205, 3210, 2), tick=2), monotonic_time=2.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3206, 3208, 0), objects=[{"effectiveName": "Tree"}], tick=3), monotonic_time=3.0)
        first = route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3197, 3244, 0), objects=[{"effectiveName": "Tree"}], tick=6), monotonic_time=4.0)
        second = route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3197, 3244, 0), objects=[{"effectiveName": "Tree"}], tick=7), monotonic_time=5.0)
        self.assertEqual(sum(1 for event in first if event["eventType"] == "arrived"), 1)
        self.assertFalse(any(event["eventType"] == "arrived" for event in second))
        self.assertFalse(any(event["eventType"] == "arrival_gate_passed" for event in second))
        self.assertGreaterEqual(state["duplicateArrivalEventsSuppressed"], 1)

    def test_arrival_state_includes_gate_fields(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        self.assertIn("arrivalGateStatus", state)
        self.assertIn("distanceToEndCluster", state)
        self.assertIn("freshEndAreaSampleCount", state)

    def test_woodcutting_area_arrives(self):
        route_tmpl = template_with_end_cluster()
        state = route_monitor.create_session_state(route_tmpl, session_id="route_unit")
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}], tick=1), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, route_tmpl, live_context(world(3195, 3244, 0), objects=[{"effectiveName": "Tree"}], tick=4), monotonic_time=2.0)
        self.assertEqual(state["routeState"], "arrived")
        self.assertEqual(len(state["completedSegments"]), 5)
        self.assertEqual(state["remainingSegments"], [])

    def test_stale_restores_previous_state_when_fresh(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        route_monitor.update_session_with_context(state, template(), live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]), monotonic_time=1.0)
        route_monitor.update_session_with_context(state, template(), live_context(world(3208, 3220, 2), age_seconds=60.0), monotonic_time=2.0)
        self.assertEqual(state["routeState"], "stale")
        route_monitor.update_session_with_context(state, template(), live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]), monotonic_time=3.0)
        self.assertEqual(state["routeState"], "ready_at_start")
        self.assertEqual(state["freshness"]["stalePeriodCount"], 1)
        self.assertGreaterEqual(state["freshness"]["longestStaleMs"], 1000)

    def test_events_include_state_change_and_segment_completed(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        events = route_monitor.update_session_with_context(
            state,
            template(),
            live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]),
            monotonic_time=1.0,
        )
        event_types = {event["eventType"] for event in events}
        self.assertIn("segment_completed", event_types)
        self.assertIn("state_change", event_types)

    def test_recent_path_is_capped(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        for index in range(5):
            route_monitor.update_session_with_context(
                state,
                template(),
                live_context(world(3208 - index, 3220 + index, 2)),
                monotonic_time=float(index + 1),
                max_recent_points=3,
            )
        self.assertEqual(len(state["recentPath"]), 3)

    def test_off_route_requires_repeated_conflicting_samples(self):
        state = route_monitor.create_session_state(template(), session_id="route_unit")
        route_monitor.update_session_with_context(state, template(), live_context(world(3300, 3300, 0)), monotonic_time=1.0)
        self.assertNotEqual(state["routeState"], "off_route")
        route_monitor.update_session_with_context(state, template(), live_context(world(3301, 3300, 0)), monotonic_time=2.0)
        self.assertEqual(state["routeState"], "off_route")
        self.assertTrue(state["offRoute"])

    def test_recording_mode_writes_route_history_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            life = lifecycle()
            comp = route_template.compare_template(template(), life, recording=recording)
            (recording / "traversal_lifecycle.json").write_text(json.dumps(life), encoding="utf-8")
            (recording / "route_template_comparison.json").write_text(json.dumps(comp), encoding="utf-8")
            state, paths, summary = route_monitor.write_recording_history(template(), recording)
            self.assertEqual(state["routeState"], "arrived")
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(paths["state"].exists())
            self.assertTrue(paths["events"].exists())
            self.assertTrue(paths["summary"].exists())

    def test_context_service_reads_route_session_state_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "route_session_state.json"
            state = route_monitor.create_session_state(template(), session_id="route_unit")
            route_monitor.update_session_with_context(state, template(), live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]), monotonic_time=1.0)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            response = context_service.build_context_response(
                live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]),
                {"schema": "context_request.v1", "needs": ["route_history"], "routeSessionStatePath": str(state_path), "responseMode": "compact"},
            )
        self.assertEqual(response["routeHistory"]["sessionId"], "route_unit")
        self.assertEqual(response["routeHistory"]["routeState"], "ready_at_start")

    def test_live_snapshot_in_bank_area_ready_at_start(self):
        status = route_monitor.monitor_live_context(
            template(),
            live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]),
        )
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["routeState"], "ready_at_start")
        self.assertEqual(status["currentArea"], "bank_area")
        self.assertEqual(status["nextExpectedSegment"]["segmentType"], "walk_segment")

    def test_live_snapshot_in_woodcutting_area_arrived(self):
        status = route_monitor.monitor_live_context(
            template_with_end_cluster(),
            live_context(world(3195, 3244, 0), objects=[{"effectiveName": "Tree"}]),
        )
        self.assertEqual(status["routeState"], "arrived")
        self.assertEqual(status["completedSegmentCount"], 5)
        self.assertEqual(status["remainingSegmentCount"], 0)

    def test_stale_telemetry_reports_stale(self):
        status = route_monitor.monitor_live_context(template(), live_context(world(3208, 3220, 2), age_seconds=60.0))
        self.assertEqual(status["status"], "WARN")
        self.assertEqual(status["routeState"], "stale")

    def test_unknown_far_area_reports_off_route(self):
        status = route_monitor.monitor_live_context(template(), live_context(world(3300, 3300, 0)))
        self.assertEqual(status["status"], "FAIL")
        self.assertEqual(status["routeState"], "off_route")
        self.assertTrue(status["offRoute"])

    def test_recording_with_pass_template_comparison_arrived(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            recording.mkdir()
            life = lifecycle()
            comp = route_template.compare_template(template(), life, recording=recording)
            (recording / "traversal_lifecycle.json").write_text(json.dumps(life), encoding="utf-8")
            (recording / "route_template_comparison.json").write_text(json.dumps(comp), encoding="utf-8")
            status = route_monitor.monitor_recording(template(), recording)
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["routeState"], "arrived")
        self.assertEqual(status["completedSegmentCount"], 5)

    def test_recording_missing_required_segment_endpoint_reached_warns(self):
        life = lifecycle(route_segments=[lifecycle()["routeSegments"][0], lifecycle()["routeSegments"][1], lifecycle()["routeSegments"][-1]])
        comp = route_template.compare_template(template(), life)
        status = route_monitor.monitor_comparison(template(), comp)
        self.assertEqual(status["status"], "WARN")
        self.assertEqual(status["routeState"], "arrived")
        self.assertGreater(status["remainingSegmentCount"], 0)

    def test_monitor_loads_template_revision_three(self):
        status = route_monitor.monitor_live_context(template(), live_context(world(3208, 3220, 2)))
        self.assertEqual(status["templateRevision"], 3)

    def test_completed_remaining_counts_at_start(self):
        status = route_monitor.monitor_live_context(template(), live_context(world(3208, 3220, 2)))
        self.assertEqual(status["completedSegmentCount"], 1)
        self.assertEqual(status["remainingSegmentCount"], 4)

    def test_wrong_endpoint_is_off_route(self):
        wrong = lifecycle(end_area="bank_area")
        comp = route_template.compare_template(template(), wrong)
        status = route_monitor.monitor_comparison(template(), comp)
        self.assertEqual(status["status"], "FAIL")
        self.assertTrue(status["offRoute"])

    def test_context_service_returns_compact_route_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "route.route_template.json"
            template_path.write_text(json.dumps(template()), encoding="utf-8")
            response = context_service.build_context_response(
                live_context(world(3208, 3220, 2), objects=[{"effectiveName": "Bank booth"}]),
                {"schema": "context_request.v1", "needs": ["route_monitor"], "routeTemplate": str(template_path), "responseMode": "compact"},
            )
        self.assertEqual(response["routeMonitor"]["routeState"], "ready_at_start")
        self.assertEqual(response["routeNextSegment"]["segmentType"], "walk_segment")

    def test_analyzer_writes_route_monitor_status_json(self):
        def snap(elapsed, tick, x, y, plane, *, objects=None):
            return {
                "event_type": "source_snapshot",
                "elapsed_seconds": elapsed,
                "latest_tick": tick,
                "high_value_fields": {
                    "latest_tick": tick,
                    "player": {"worldPoint": world(x, y, plane)},
                    "nearby_objects": objects or [],
                    "route_objects": objects or [],
                },
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            events = [
                snap(0, 1, 3208, 3220, 2, objects=[{"effectiveName": "Bank booth"}]),
                snap(2, 2, 3205, 3209, 2),
                snap(4, 3, 3206, 3208, 0),
                snap(5, 4, 3195, 3244, 0, objects=[{"effectiveName": "Tree"}]),
            ]
            with (recording / "events.jsonl").open("w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event) + "\n")
            life = traversal_lifecycle.analyze_data(events=events, recording_path=recording)
            route_tmpl = route_template.extract_template(life)
            template_path = root / "template.json"
            template_path.write_text(json.dumps(route_tmpl), encoding="utf-8")
            code = analyze_manual_recording.main([
                str(recording),
                "--summary",
                "--schema-gap",
                "--traversal-lifecycle",
                "--group-traversal-steps",
                "--compare-route-template",
                str(template_path),
                "--route-monitor",
                "--route-monitor-template",
                str(template_path),
                "--print-route-monitor",
            ])
            self.assertEqual(code, 0)
            self.assertTrue((recording / "route_monitor_status.json").exists())

    def test_analyzer_writes_route_history_files(self):
        def snap(elapsed, tick, x, y, plane, *, objects=None):
            return {
                "event_type": "source_snapshot",
                "elapsed_seconds": elapsed,
                "latest_tick": tick,
                "high_value_fields": {
                    "latest_tick": tick,
                    "player": {"worldPoint": world(x, y, plane)},
                    "nearby_objects": objects or [],
                    "route_objects": objects or [],
                },
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "recording"
            recording.mkdir()
            events = [
                snap(0, 1, 3208, 3220, 2, objects=[{"effectiveName": "Bank booth"}]),
                snap(2, 2, 3205, 3209, 2),
                snap(4, 3, 3206, 3208, 0),
                snap(5, 4, 3195, 3244, 0, objects=[{"effectiveName": "Tree"}]),
            ]
            with (recording / "events.jsonl").open("w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event) + "\n")
            life = traversal_lifecycle.analyze_data(events=events, recording_path=recording)
            route_tmpl = route_template.extract_template(life)
            template_path = root / "template.json"
            template_path.write_text(json.dumps(route_tmpl), encoding="utf-8")
            code = analyze_manual_recording.main([
                str(recording),
                "--summary",
                "--schema-gap",
                "--traversal-lifecycle",
                "--group-traversal-steps",
                "--compare-route-template",
                str(template_path),
                "--route-history",
                "--route-monitor-template",
                str(template_path),
                "--print-route-history",
            ])
            self.assertEqual(code, 0)
            self.assertTrue((recording / "route_session_state.json").exists())
            self.assertTrue((recording / "route_history_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
