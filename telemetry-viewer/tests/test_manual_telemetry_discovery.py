import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import context_service
import manual_recorder
import telemetry_schema
import telemetry_sources


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


class ManualTelemetryDiscoveryTest(unittest.TestCase):
    def _bank_ui_server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                _ = self.rfile.read(int(self.headers.get("Content-Length") or "0"))
                payload = {
                    "schema": "plugin_snapshot_response.v1",
                    "generatedAtUtc": "2026-06-07T16:00:00Z",
                    "latestTick": 123,
                    "status": "PASS",
                    "freshness": {
                        "fresh": True,
                        "latestTick": 123,
                        "ageMillisByNeed": {"bank_ui": 40},
                    },
                    "payloads": {
                        "bank_ui": {
                            "schema": "bank_ui_context_payload.v1",
                            "bankOpen": True,
                            "depositBoxOpen": False,
                            "bankRootVisible": True,
                            "bankContainerVisible": True,
                            "bankSummary": {
                                "known": True,
                                "itemCount": 1,
                                "filledSlots": 1,
                                "totalQuantityByItemId": {"1511": 6},
                            },
                            "bankItems": [{"slot": 1, "itemId": 1511, "name": "Logs", "quantity": 6}],
                            "inventorySummary": {"known": True, "freeSlots": 16, "items": []},
                        }
                    },
                    "warnings": [],
                    "missingCapabilities": [],
                }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):  # noqa: D102
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def test_tolerant_json_reading_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good.json"
            good.write_text(json.dumps({"latestTick": 12, "gameState": "LOGGED_IN"}), encoding="utf-8")

            read = telemetry_sources.read_source(good, name="good")
            self.assertEqual(read["parse_status"], "ok")
            self.assertEqual(read["data"]["latestTick"], 12)

            missing = telemetry_sources.read_source(root / "missing.json", name="missing")
            self.assertFalse(missing["exists"])
            self.assertEqual(missing["parse_status"], "missing")

    def test_partial_bad_json_handling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad.json"
            bad.write_text('{"latestTick": 12', encoding="utf-8")
            read = telemetry_sources.read_source(bad, name="bad")
            self.assertEqual(read["parse_status"], "json_error")
            self.assertIn("JSONDecodeError", read["read_error"])

            jsonl = root / "events.jsonl"
            jsonl.write_text('{"ok":1}\n{"bad"\n{"ok":2}\n', encoding="utf-8")
            read_jsonl = telemetry_sources.read_source(jsonl, name="events")
            self.assertEqual(read_jsonl["parse_status"], "partial")
            self.assertEqual(read_jsonl["record_count"], 2)
            self.assertEqual(read_jsonl["malformed_line_count"], 1)

    def test_plugin_snapshot_bank_ui_reader(self):
        server = self._bank_ui_server()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/snapshot"
            read = telemetry_sources.read_plugin_snapshot_need("bank_ui", snapshot_url=url, timeout_seconds=1.0)
            self.assertEqual(read["parse_status"], "ok")
            self.assertTrue(read["exists"])
            self.assertTrue(read["data"]["bankOpen"])
            self.assertEqual(read["latest_tick"], 123)
            self.assertEqual(read["age_seconds"], 0.04)
        finally:
            server.shutdown()
            server.server_close()

    def test_source_snapshot_event_preserves_bank_ui_payload(self):
        server = self._bank_ui_server()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                status = root / "status.json"
                status.write_text(json.dumps({"latestTickProcessed": 123}), encoding="utf-8")
                url = f"http://127.0.0.1:{server.server_address[1]}/snapshot"
                event = manual_recorder.source_snapshot_event(
                    {"status": status},
                    ["status"],
                    session_id="s1",
                    started_monotonic=time.monotonic(),
                    max_bytes=telemetry_sources.DEFAULT_MAX_BYTES,
                    include_raw=False,
                    plugin_snapshot_needs=["bank_ui"],
                    plugin_snapshot_url=url,
                    plugin_snapshot_timeout_seconds=1.0,
                )
                bank_sources = [source for source in event["sources"] if source.get("name") == "bank_ui"]
                self.assertEqual(len(bank_sources), 1)
                self.assertTrue(bank_sources[0]["data"]["bankOpen"])
                self.assertIn("bank_ui", event["changed_sources"])
                self.assertTrue(event["high_value_fields"]["bank"]["bankOpen"])
                self.assertIn("bank_ui", event["available_fields"])
        finally:
            server.shutdown()
            server.server_close()

    def test_field_presence_scanning_and_gap_categories(self):
        payload = {
            "latestTick": 44,
            "gameState": "LOGGED_IN",
            "inventoryState": {"items": [{"slot": 0, "itemId": 1511, "quantity": 1}], "freeSlots": 27},
            "objects": [{"objectKey": "tree-1", "name": "Tree", "actions": ["Chop down"], "worldX": 3200, "worldY": 3201, "plane": 0}],
        }
        scan = telemetry_schema.scan_field_presence(payload)
        self.assertIn("tick", scan["available_fields"])
        self.assertIn("inventory", scan["available_fields"])
        self.assertIn("selected_item_spell_widget_state", scan["missing_fields"])

        categories = telemetry_schema.categorize_schema_gaps(scan)
        self.assertIn("tick", categories["present"])
        self.assertIn("selected_item_spell_widget_state", categories["requires_bridge_export"])

    def test_route_object_normalization_and_selection(self):
        payload = {
            "status": {
                "worldModelRouteObjectCensus": {
                    "objects": [
                        {
                            "objectKey": "0:3204:3229:stair",
                            "kind": "GAME_OBJECT",
                            "id": 56230,
                            "objectName": "Staircase",
                            "actions": ["Climb-up", "Top-floor"],
                            "worldX": 3204,
                            "worldY": 3229,
                            "plane": 0,
                            "distanceToPlayer": 1,
                            "routeObjectCandidate": True,
                            "routeObjectKind": "route_transition",
                        }
                    ]
                }
            }
        }
        model = telemetry_schema.normalized_telemetry(payload)
        self.assertEqual(model["route_objects"][0]["effectiveName"], "Staircase")
        self.assertEqual(model["route_objects"][0]["effectiveActions"], ["Climb-up", "Top-floor"])

        selected = telemetry_schema.select_target(model, kind="route", query="climb", mode="nearest")
        self.assertEqual(selected["status"], "PASS")
        self.assertEqual(selected["candidate"]["effectiveId"], 56230)
        self.assertEqual(selected["candidate"]["routeObjectKind"], "route_transition")

    def test_analyzer_summary_from_synthetic_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "20260602_190000_test"
            events = [
                {
                    "schema_version": "manual_telemetry_event.v1",
                    "event_type": "recording_start",
                    "session_id": "s1",
                    "wall_time_utc": "2026-06-02T00:00:00Z",
                    "elapsed_seconds": 0,
                    "label": "test",
                },
                {
                    "schema_version": "manual_telemetry_event.v1",
                    "event_type": "source_snapshot",
                    "session_id": "s1",
                    "wall_time_utc": "2026-06-02T00:00:01Z",
                    "elapsed_seconds": 1,
                    "latest_tick": 10,
                    "sources": [{"name": "status", "path": "status.json", "exists": True, "parse_status": "ok", "stale": False}],
                    "field_presence": {"available_fields": ["tick", "inventory"], "missing_fields": ["bank_container"]},
                    "available_fields": ["tick", "inventory"],
                    "missing_fields": ["bank_container"],
                    "high_value_fields": {"latest_tick": 10, "inventory": {"itemCount": 1, "freeSlots": 27}},
                },
                {
                    "schema_version": "manual_telemetry_event.v1",
                    "event_type": "manual_marker",
                    "session_id": "s1",
                    "wall_time_utc": "2026-06-02T00:00:01Z",
                    "elapsed_seconds": 1.2,
                    "label": "clicked",
                },
                {
                    "schema_version": "manual_telemetry_event.v1",
                    "event_type": "source_snapshot",
                    "session_id": "s1",
                    "wall_time_utc": "2026-06-02T00:00:02Z",
                    "elapsed_seconds": 2,
                    "latest_tick": 11,
                    "sources": [{"name": "status", "path": "status.json", "exists": True, "parse_status": "json_error", "read_error": "partial", "stale": True}],
                    "field_presence": {"available_fields": ["tick", "inventory"], "missing_fields": ["bank_container"]},
                    "available_fields": ["tick", "inventory"],
                    "missing_fields": ["bank_container"],
                    "high_value_fields": {"latest_tick": 11, "inventory": {"itemCount": 2, "freeSlots": 26}},
                },
                {
                    "schema_version": "manual_telemetry_event.v1",
                    "event_type": "recording_stop",
                    "session_id": "s1",
                    "wall_time_utc": "2026-06-02T00:00:03Z",
                    "elapsed_seconds": 3,
                    "duration_seconds": 3,
                },
            ]
            write_jsonl(recording / "events.jsonl", events)

            summary = analyze_manual_recording.analyze_recording(recording)
            self.assertEqual(summary["snapshot_count"], 2)
            self.assertEqual(summary["tick_range"]["first"], 10)
            self.assertIn("inventory_count", summary["fields_changed"])
            self.assertEqual(summary["markers"][0]["label"], "clicked")
            self.assertEqual(summary["source_freshness"]["parse_failure_count"], 1)

    def test_compact_context_response_filters_requested_sections(self):
        context = {
            "session": Path("synthetic"),
            "baseline": {
                "latestTick": 5,
                "gameState": "LOGGED_IN",
                "player": {"worldX": 3200, "worldY": 3200, "plane": 0, "runEnergy": 70},
            },
            "status": {"latestTickProcessed": 5, "compactPacketLastSequence": 99},
            "activity": {
                "inventoryState": {"known": True, "itemCount": 1, "freeSlots": 27, "items": [{"slot": 0, "itemId": 1511}]}
            },
            "navigation": {},
            "watchValues": {},
            "events": [],
            "candidates": [],
            "warnings": [],
            "missingFields": [],
            "sourceFiles": [],
        }
        response = context_service.build_context_response(
            context,
            {"schema": "context_request.v1", "needs": ["baseline", "inventory"], "responseMode": "compact"},
        )
        self.assertIn("baseline", response)
        self.assertIn("inventory", response)
        self.assertNotIn("bank", response)
        self.assertEqual(response["latestExportSequence"], 99)

        bank_response = context_service.build_context_response(
            context,
            {"schema": "context_request.v1", "needs": ["bank"], "responseMode": "compact"},
        )
        self.assertIn("bank", bank_response)
        self.assertIn("bank", bank_response["missingCapabilities"])
        self.assertEqual(bank_response["status"], "WARN")

    def test_compact_context_response_includes_route_objects_and_route_helper(self):
        context = {
            "session": Path("synthetic"),
            "baseline": {
                "latestTick": 5,
                "gameState": "LOGGED_IN",
                "player": {"worldX": 3205, "worldY": 3228, "plane": 0},
            },
            "status": {
                "latestTickProcessed": 5,
                "compactPacketLastSequence": 99,
                "worldModelRouteObjectCensus": {
                    "objects": [
                        {
                            "objectKey": "0:3204:3229:stair",
                            "kind": "GAME_OBJECT",
                            "id": 56230,
                            "objectName": "Staircase",
                            "actions": ["Climb-up", "Top-floor"],
                            "worldX": 3204,
                            "worldY": 3229,
                            "plane": 0,
                            "distanceToPlayer": 1,
                            "routeObjectCandidate": True,
                            "routeObjectKind": "route_transition",
                        }
                    ]
                },
            },
            "activity": {},
            "navigation": {},
            "watchValues": {},
            "events": [],
            "candidates": [],
            "warnings": [],
            "missingFields": [],
            "sourceFiles": [],
        }
        response = context_service.build_context_response(
            context,
            {
                "schema": "context_request.v1",
                "needs": ["route_objects", "nearest:route:staircase"],
                "responseMode": "compact",
                "maxCandidates": 2,
            },
        )
        self.assertEqual(response["routeObjects"]["count"], 1)
        self.assertEqual(response["nearestTargets"]["route"]["staircase"]["candidate"]["effectiveName"], "Staircase")
        self.assertEqual(response["nearestTargets"]["route"]["staircase"]["candidate"]["routeObjectKind"], "route_transition")


if __name__ == "__main__":
    unittest.main()
