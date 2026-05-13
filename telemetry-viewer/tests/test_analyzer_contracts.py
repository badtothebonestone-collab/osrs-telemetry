import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import capabilities
import live_context_format
from analyzers import live_state


ANALYZER_DIR = VIEWER_DIR / "analyzers"
ANALYZER_FILES = [
    path
    for path in ANALYZER_DIR.glob("*.py")
    if path.name not in {"__init__.py", "live_state.py"}
]


class AnalyzerContractsTest(unittest.TestCase):
    def test_common_contract_types_exist(self):
        self.assertTrue(hasattr(live_state, "AnalyzerResult"))
        self.assertTrue(hasattr(live_state, "AnalyzerWarning"))
        self.assertTrue(hasattr(live_state, "AnalyzerTiming"))
        self.assertTrue(hasattr(live_state, "CapabilityStatus"))
        self.assertTrue(hasattr(live_state, "MissingCapability"))
        result = live_state.AnalyzerResult(source_tick=123, retained_from_previous=True)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.source_tick, 123)
        self.assertTrue(result.retained_from_previous)

    def test_analyzer_contexts_have_common_schema_fields(self):
        context_classes = [
            live_state.InventoryContext,
            live_state.TargetContext,
            live_state.NavigationContext,
            live_state.NavigationIntentContext,
            live_state.ActivityContext,
            live_state.IntentOverlayContext,
            live_state.BrainContext,
            live_state.ServiceContext,
            live_state.ProcessInventoryContext,
        ]
        for context_class in context_classes:
            with self.subTest(context=context_class.__name__):
                context = context_class()
                self.assertTrue(hasattr(context, "status"))
                self.assertTrue(hasattr(context, "warnings"))
                self.assertTrue(hasattr(context, "missing_capabilities"))
                self.assertTrue(hasattr(context, "source_tick"))
                self.assertTrue(hasattr(context, "retained_from_previous"))
                self.assertTrue(hasattr(context, "timing_millis"))
                self.assertEqual(context.missingCapabilities, [])
                self.assertIsNone(context.sourceTick)
                self.assertFalse(context.retainedFromPrevious)
                self.assertIsNone(context.timingMillis)

    def test_capability_aliases_are_normalized_and_deduped(self):
        normalized = capabilities.normalize_capability_names(
            [
                "inventoryDeltas",
                "inventory.deltas",
                "animationFrame",
                "activity.animation_frame",
                "explicitMovementState",
                "fullPathfinding",
                "watch_values",
            ]
        )

        self.assertEqual(
            normalized,
            [
                "inventory.deltas",
                "activity.animation_frame",
                "activity.explicit_movement_state",
                "navigation.full_pathfinding",
                "plugin_snapshot.watch_values",
            ],
        )

    def test_daily_human_output_deduplicates_normalized_capabilities(self):
        response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "missingCapabilities": [
                "inventoryDeltas",
                "inventory.deltas",
                "animationFrame",
                "activity.animation_frame",
                "explicitMovementState",
                "activity.explicit_movement_state",
            ],
            "baseline": {"player": {"worldX": 1, "worldY": 2, "plane": 0}},
            "inventory": {"freeSlots": 27, "inventoryFull": False},
            "activity": {"apparentState": "idle"},
        }

        output = live_context_format.format_context_human(response, compact=False)

        self.assertEqual(output.count("inventory change tracking is not available yet"), 1)
        self.assertEqual(output.count("animation frame detail is unavailable"), 1)
        self.assertEqual(output.count("explicit movement state is unavailable"), 1)

    def test_analyzers_do_not_use_side_effect_sources_or_sinks(self):
        forbidden_tokens = [
            "open(",
            ".open(",
            "Path.write_",
            "write_text(",
            "write_bytes(",
            "urllib",
            "requests",
            "http.client",
            "socket",
            "subprocess",
            "Popen",
            "ThreadingHTTPServer",
            "HTTPServer",
            "live_packet_reader",
            "LivePacketReader",
            "PluginSnapshotEndpoint",
            "compact_packets",
        ]
        for path in ANALYZER_FILES:
            source = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                for token in forbidden_tokens:
                    self.assertNotIn(token, source)

    def test_contract_objects_do_not_emit_action_fields(self):
        banned = {"action", "actions", "input", "click", "mouse", "keyboard", "menu", "invoke", "execute"}
        contexts = [
            live_state.InventoryContext(),
            live_state.TargetContext(),
            live_state.NavigationContext(),
            live_state.NavigationIntentContext(),
            live_state.ActivityContext(),
            live_state.IntentOverlayContext(),
            live_state.BrainContext(),
            live_state.ServiceContext(),
            live_state.ProcessInventoryContext(),
        ]

        for context in contexts:
            with self.subTest(context=type(context).__name__):
                keys = set(context.__dict__.keys())
                self.assertFalse(keys.intersection(banned))


if __name__ == "__main__":
    unittest.main()
