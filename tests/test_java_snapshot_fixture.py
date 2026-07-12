from __future__ import annotations

import json
import unittest
from pathlib import Path

from osrs_bot.observation import parse_observation


FIXTURE = Path(__file__).parent / "fixtures" / "java_snapshot_endpoint.json"
CORE_FACTS = ("baseline", "inventory", "activity", "bank_ui", "dialogue_state")


class JavaSnapshotFixtureTests(unittest.TestCase):
    def test_real_java_endpoint_fixture_parses_as_a_loaded_observation(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

        observation = parse_observation(payload)

        self.assertEqual("plugin_snapshot_response.v2", payload["schema"])
        self.assertEqual("sensor_frame.v1", payload["sensorFrame"]["schema"])
        self.assertTrue(observation.source_coherent)
        self.assertTrue(observation.loaded_scene)
        self.assertEqual(4_242, observation.tick)
        self.assertEqual("java-fixture-session", observation.session_id)
        self.assertEqual((3_192, 3_244, 0), (
            observation.location.x,
            observation.location.y,
            observation.location.plane,
        ))

    def test_java_fixture_contains_serialized_fact_sizes_not_placeholders(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        facts = payload["sensorFrame"]["facts"]

        self.assertEqual(set(CORE_FACTS), set(facts))
        for fact_name in CORE_FACTS:
            with self.subTest(fact=fact_name):
                self.assertTrue(facts[fact_name]["available"])
                self.assertGreater(facts[fact_name]["sizeBytes"], 1)


if __name__ == "__main__":
    unittest.main()
