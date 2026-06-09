import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import context_service
import interruption_lifecycle
import task_script_api
import woodcutting_lifecycle


def woodcutting_gap_lifecycle() -> dict:
    return {
        "schema": woodcutting_lifecycle.SCHEMA_VERSION,
        "status": "PASS",
        "phase": "chopping",
        "clicks": {
            "freshChopClicks": [
                {"tick": 100, "elapsedSeconds": 10.0, "option": "Chop down", "targetName": "Tree"},
                {"tick": 110, "elapsedSeconds": 18.0, "option": "Chop down", "targetName": "Tree"},
                {"tick": 240, "elapsedSeconds": 110.0, "option": "Chop down", "targetName": "Tree"},
            ]
        },
        "animation": {"activeTicks": [102, 109, 242]},
        "warnings": [],
        "evidence": ["synthetic resumed woodcutting"],
    }


def woodcutting_continues_after_combat_lifecycle() -> dict:
    return {
        "schema": woodcutting_lifecycle.SCHEMA_VERSION,
        "status": "PASS",
        "phase": "chopping",
        "clicks": {
            "freshChopClicks": [
                {"tick": 131, "elapsedSeconds": 5.0, "option": "Chop down", "targetName": "Tree"},
            ]
        },
        "animation": {"activeTicks": [131, 141, 194, 214]},
        "cycles": [
            {"startTick": 131, "endTick": 141, "logGainTick": 141, "logsGained": 1},
            {"startTick": 131, "endTick": 204, "logGainTick": 204, "logsGained": 1},
        ],
        "warnings": [],
        "evidence": ["synthetic woodcutting continued after combat"],
    }


def combat_snapshot(**overrides) -> dict:
    payload = {
        "schema": "combat_state.v1",
        "tick": 120,
        "inCombat": True,
        "playerInteracting": {},
        "actorsInteractingWithPlayer": [
            {"name": "Mugger", "type": "NPC", "id": 175, "combatLevel": 6}
        ],
        "nearbyHostileNpcs": [],
        "recentHitsplats": [
            {"eventType": "HitsplatApplied", "actor": {"name": "Local player", "type": "LOCAL_PLAYER"}, "amount": 2, "tick": 121}
        ],
        "recentChatMessages": [],
        "recentStatChanges": [],
        "playerHealth": {"ratio": 8, "scale": 10, "boostedHitpoints": 8, "realHitpoints": 10},
        "warnings": [],
    }
    payload.update(overrides)
    return {
        "event_type": "source_snapshot",
        "elapsed_seconds": 30.0,
        "latest_tick": payload["tick"],
        "sources": [{"name": "combat_state", "data": payload, "parse_status": "ok"}],
    }


class InterruptionLifecycleTest(unittest.TestCase):
    def test_task_gap_without_combat_is_unknown_warn(self):
        lifecycle = interruption_lifecycle.analyze_data(events=[], woodcutting_lifecycle=woodcutting_gap_lifecycle())
        self.assertEqual(lifecycle["status"], "WARN")
        self.assertTrue(lifecycle["interruptionDetected"])
        self.assertEqual(lifecycle["interruptionType"], "unknown")
        self.assertEqual(lifecycle["primaryCause"], "unknown")
        self.assertTrue(lifecycle["taskResumed"])
        self.assertIn("combat_state", lifecycle["missingCapabilities"])

    def test_npc_targeting_player_classifies_combat(self):
        lifecycle = interruption_lifecycle.analyze_data(events=[combat_snapshot()], woodcutting_lifecycle=woodcutting_gap_lifecycle())
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertEqual(lifecycle["interruptionType"], "combat")
        self.assertEqual(lifecycle["primaryCause"], "mugger_attack")
        self.assertTrue(lifecycle["combat"]["npcTargetedPlayer"])

    def test_hitsplat_on_local_player_is_strong_combat_evidence(self):
        lifecycle = interruption_lifecycle.analyze_data(events=[combat_snapshot()], woodcutting_lifecycle=woodcutting_gap_lifecycle())
        self.assertTrue(lifecycle["combat"]["combatObserved"])
        self.assertEqual(lifecycle["combat"]["hitsplatsSeen"], 1)
        self.assertEqual(lifecycle["combatDamageSummary"]["damageTakenTotal"], 2)
        self.assertGreaterEqual(lifecycle["confidence"], 0.9)

    def test_post_combat_woodcutting_evidence_proves_task_resumed(self):
        combat = combat_snapshot(
            tick=150,
            recentHitsplats=[
                {"eventType": "HitsplatApplied", "actor": {"name": "Local player", "type": "LOCAL_PLAYER"}, "amount": 2, "tick": 151}
            ],
        )
        clear = combat_snapshot(
            tick=184,
            inCombat=False,
            actorsInteractingWithPlayer=[],
            playerInteracting={},
            recentHitsplats=[],
            recentStatChanges=[],
            playerHealth={"ratio": 8, "scale": 10, "boostedHitpoints": 8, "realHitpoints": 10},
        )
        clear["elapsed_seconds"] = 45.0
        lifecycle = interruption_lifecycle.analyze_data(
            events=[combat, clear],
            woodcutting_lifecycle=woodcutting_continues_after_combat_lifecycle(),
        )
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertEqual(lifecycle["interruptionType"], "combat")
        self.assertTrue(lifecycle["taskResumed"])
        self.assertEqual(lifecycle["taskInterruptedAt"]["tick"], 150)
        self.assertEqual(lifecycle["taskResumedAt"]["tick"], 194)

    def test_stat_and_chat_messages_classify_level_up_without_combat(self):
        event = combat_snapshot(
            inCombat=False,
            actorsInteractingWithPlayer=[],
            recentHitsplats=[],
            recentChatMessages=[{"eventType": "ChatMessage", "message": "Congratulations, you just advanced an Attack level.", "tick": 125}],
            recentStatChanges=[{"eventType": "StatChanged", "skill": "ATTACK", "level": 2, "tick": 125}],
        )
        lifecycle = interruption_lifecycle.analyze_data(events=[event], woodcutting_lifecycle=woodcutting_gap_lifecycle())
        self.assertEqual(lifecycle["interruptionType"], "level_up")
        self.assertEqual(lifecycle["primaryCause"], "level_up")
        self.assertTrue(lifecycle["statChanges"])

    def test_woodcutting_lifecycle_attaches_interruption_summary(self):
        interruption = interruption_lifecycle.analyze_data(events=[combat_snapshot()], woodcutting_lifecycle=woodcutting_gap_lifecycle())
        attached = woodcutting_lifecycle.attach_interruption(woodcutting_gap_lifecycle(), interruption)
        self.assertEqual(attached["interruption"]["interruptionType"], "combat")
        self.assertTrue(attached["interruption"]["taskResumed"])

    def test_context_service_returns_compact_interruption(self):
        response = context_service.build_context_response(
            {
                "status": {"latestTickProcessed": 120, "generatedAtUtc": datetime.now(timezone.utc).isoformat()},
                "baseline": {"generatedAtUtc": datetime.now(timezone.utc).isoformat()},
                "candidates": [],
                "combat_state": combat_snapshot()["sources"][0]["data"],
            },
            {"schema": context_service.REQUEST_SCHEMA, "needs": ["combat_state", "interruption_lifecycle"], "responseMode": "compact"},
        )
        self.assertIn("interruptionLifecycle", response)
        self.assertIn(response["status"], {"PASS", "WARN"})

    def test_task_script_api_exposes_interruption_cause(self):
        lifecycle = interruption_lifecycle.analyze_data(events=[combat_snapshot()], woodcutting_lifecycle=woodcutting_gap_lifecycle())
        self.assertTrue(task_script_api.was_task_interrupted(lifecycle))
        self.assertEqual(task_script_api.get_interruption_cause(lifecycle), "mugger_attack")
        self.assertTrue(task_script_api.is_in_combat(lifecycle))


if __name__ == "__main__":
    unittest.main()
