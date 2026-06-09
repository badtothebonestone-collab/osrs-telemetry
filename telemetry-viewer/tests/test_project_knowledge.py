import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import update_project_knowledge


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ProjectKnowledgeTest(unittest.TestCase):
    def test_check_passes(self):
        result = update_project_knowledge.check_knowledge()
        self.assertIn(result["status"], {"PASS", "WARN"})
        self.assertGreaterEqual(result["capabilityCount"], 1)
        self.assertIn("project_knowledge.json", result["indexFiles"])
        self.assertIn("ENTRYPOINTS.md", result["requiredDocFiles"])
        self.assertTrue(result["scriptApiMapIndexed"])
        self.assertTrue(result["openGapsIndexed"])
        self.assertFalse(result["missingKeyCapabilities"])

    def test_recordings_index_can_index_synthetic_recording_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "20260607_120000_unit_bank"
            write_json(
                recording / "summary.json",
                {
                    "recording_id": recording.name,
                    "status": "PASS",
                    "banking_lifecycle": {
                        "status": "PASS",
                        "bank": {"bankUiPresent": True, "bankUiSnapshotCount": 2, "openSeen": True, "containerAvailable": True},
                        "deposit": {"detected": True, "items": [{"id": 1511, "name": "Logs", "quantity": 3}]},
                        "bankContainerDeltaAvailable": True,
                        "depositConfirmationLevel": "bank_container_delta_confirmed",
                    },
                },
            )
            entries = update_project_knowledge.scan_recordings(recording)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["activityType"], "Banking")
        self.assertEqual(entries[0]["verdict"], "PASS")
        self.assertTrue(entries[0]["goodFixture"])
        self.assertTrue(any("bank_ui" in item for item in entries[0]["usefulEvidence"]))

    def test_capability_registry_records_present_and_missing_capabilities(self):
        model = update_project_knowledge.build_project_knowledge()
        capability_ids = {item["id"] for item in model["capabilities"]}
        gap_ids = {item["id"] for item in model["gaps"]}
        self.assertIn("bank_ui_preservation", capability_ids)
        self.assertIn("bank_container_delta", capability_ids)
        self.assertIn("combat_damage_summary", capability_ids)
        self.assertIn("woodcutting_loop_lifecycle", capability_ids)
        self.assertIn("woodcutting_target_geometry", capability_ids)
        self.assertIn("human_click_planning", capability_ids)
        self.assertIn("bot_eval_runner", capability_ids)
        self.assertIn("loaded_scene_recovery", capability_ids)
        self.assertIn("route_demonstration_guide", capability_ids)
        self.assertIn("selected_item_spell_widget", gap_ids)
        self.assertIn("validate_human_click_plans_against_recordings", gap_ids)
        self.assertNotIn("full_woodcutting_loop_fixture", gap_ids)

    def test_open_gaps_include_missing_capabilities_from_schema_gap(self):
        model = update_project_knowledge.build_project_knowledge()
        gap = next(item for item in model["gaps"] if item["id"] == "selected_item_spell_widget")
        self.assertEqual(gap["requiredLayer"], "plugin")
        self.assertEqual(gap["status"], "open")

    def test_api_data_path_represents_plugin_to_script(self):
        model = update_project_knowledge.build_project_knowledge()
        banking = next(item for item in model["apiDataPaths"] if item["family"] == "banking")
        self.assertIn("TelemetryPlugin", banking["pluginLiveSource"])
        self.assertIn("events.jsonl", banking["recorderArtifact"])
        self.assertIn("banking_lifecycle.json", banking["analyzerOutput"])
        self.assertIn("deposit_result", banking["contextField"])
        self.assertIn("get_deposit_result", banking["scriptApi"])
        combat = next(item for item in model["apiDataPaths"] if item["family"] == "combat/interruption")
        self.assertIn("combat_damage_summary.json", combat["analyzerOutput"])
        self.assertIn("damage_taken", combat["contextField"])
        self.assertIn("get_combat_damage_summary", combat["scriptApi"])
        loop = next(item for item in model["apiDataPaths"] if item["family"] == "woodcutting_loop")
        self.assertIn("woodcutting_loop_lifecycle.json", loop["analyzerOutput"])
        self.assertIn("next_expected_phase", loop["contextField"])
        self.assertIn("get_woodcutting_loop_lifecycle", loop["scriptApi"])
        planning = next(item for item in model["apiDataPaths"] if item["family"] == "human_click_planning")
        self.assertIn("click_plan", planning["contextField"])
        self.assertIn("get_human_click_plan", planning["scriptApi"])

    def test_analyzer_update_knowledge_writes_summary_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = root / "20260607_130000_unit_recording"
            recording.mkdir()
            events = [
                {"event_type": "recording_start", "elapsed_seconds": 0, "wall_time_utc": "2026-06-07T13:00:00Z", "label": "unit"},
                {"event_type": "recording_stop", "elapsed_seconds": 1, "duration_seconds": 1, "wall_time_utc": "2026-06-07T13:00:01Z"},
            ]
            (recording / "events.jsonl").write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
            out = root / "kb"
            exit_code = analyze_manual_recording.main([str(recording), "--update-knowledge", "--knowledge-out", str(out), "--out", str(root / "analysis.json")])
            self.assertEqual(exit_code, 0)
            summary = json.loads((recording / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["knowledgeUpdated"])
            self.assertTrue(summary["recordingIndexed"])
            self.assertTrue((out / "project_knowledge.json").exists())

    def test_manual_notes_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / "docs"
            kb = Path(tmp) / "kb"
            model = update_project_knowledge.build_project_knowledge()
            update_project_knowledge.update_knowledge(knowledge_out=kb, docs_out=docs, write_docs_flag=True)
            project_state = docs / "PROJECT_STATE.md"
            text = project_state.read_text(encoding="utf-8").replace(
                update_project_knowledge.MANUAL_BEGIN + "\n" + update_project_knowledge.MANUAL_END,
                update_project_knowledge.MANUAL_BEGIN + "\nkeep this manual note\n" + update_project_knowledge.MANUAL_END,
            )
            project_state.write_text(text, encoding="utf-8")
            update_project_knowledge.write_docs(model, docs_out=docs)
            self.assertIn("keep this manual note", project_state.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
