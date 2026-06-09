import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import combat_damage_summary
import context_service
import interruption_lifecycle
import task_script_api


def combat_event(
    tick: int,
    *,
    in_combat: bool = True,
    hitsplats: list[dict] | None = None,
    health: dict | None = None,
    actors: list[dict] | None = None,
    player_target: dict | None = None,
    deaths: list[dict] | None = None,
) -> dict:
    payload = {
        "schema": "combat_state.v1",
        "tick": tick,
        "inCombat": in_combat,
        "playerInteracting": player_target if player_target is not None else {"name": "Mugger", "type": "NPC", "id": 513},
        "actorsInteractingWithPlayer": actors if actors is not None else [{"name": "Mugger", "type": "NPC", "id": 513}],
        "recentHitsplats": hitsplats or [],
        "recentActorDeaths": deaths or [],
        "recentChatMessages": [],
        "recentStatChanges": [],
        "playerHealth": health or {"boostedHitpoints": 10, "realHitpoints": 11, "ratio": 24, "scale": 30},
    }
    return {
        "event_type": "source_snapshot",
        "elapsed_seconds": tick / 2,
        "latest_tick": tick,
        "sources": [{"name": "combat_state", "data": payload, "parse_status": "ok"}],
    }


def sample_events() -> list[dict]:
    return [
        combat_event(
            10,
            hitsplats=[
                {"eventType": "HitsplatApplied", "tick": 10, "actor": {"name": "Local player", "type": "LOCAL_PLAYER"}, "amount": 2},
                {"eventType": "HitsplatApplied", "tick": 10, "actor": {"name": "Mugger", "type": "NPC", "id": 513, "interacting": {"name": "Local player", "type": "LOCAL_PLAYER"}}, "amount": 3},
            ],
            health={"boostedHitpoints": 10, "realHitpoints": 11, "ratio": 24, "scale": 30},
        ),
        combat_event(
            14,
            hitsplats=[
                {"eventType": "HitsplatApplied", "tick": 14, "actor": {"name": "Local player", "type": "LOCAL_PLAYER"}, "amount": 1},
                {"eventType": "HitsplatApplied", "tick": 14, "actor": {"name": "Mugger", "type": "NPC", "id": 513, "interacting": {"name": "Local player", "type": "LOCAL_PLAYER"}}, "amount": 4},
            ],
            health={"boostedHitpoints": 7, "realHitpoints": 11, "ratio": 18, "scale": 30},
            deaths=[{"eventType": "ActorDeath", "tick": 15, "name": "Mugger", "type": "NPC", "id": 513}],
        ),
        combat_event(18, in_combat=False, hitsplats=[], health={"boostedHitpoints": 7, "realHitpoints": 11, "ratio": 18, "scale": 30}, actors=[], player_target={}),
    ]


class CombatDamageSummaryTest(unittest.TestCase):
    def test_local_player_hitsplat_amount_contributes_to_damage_taken(self):
        summary = combat_damage_summary.analyze_data(events=sample_events())
        self.assertEqual(summary["damageTaken"]["total"], 3)
        self.assertEqual(summary["damageTaken"]["hitsplatCount"], 2)

    def test_npc_hitsplat_contributes_to_damage_dealt(self):
        summary = combat_damage_summary.analyze_data(events=sample_events())
        self.assertEqual(summary["damageDealt"]["total"], 7)
        self.assertEqual(summary["damageDealt"]["hitsplatCount"], 2)

    def test_hp_decrease_and_actor_death_are_captured(self):
        summary = combat_damage_summary.analyze_data(events=sample_events())
        self.assertTrue(summary["health"]["healthChanged"])
        self.assertEqual(summary["health"]["hpBefore"], 10)
        self.assertEqual(summary["health"]["hpAfter"], 7)
        self.assertTrue(summary["actorDeaths"])

    def test_primary_opponent_and_combat_window_are_inferred(self):
        summary = combat_damage_summary.analyze_data(events=sample_events())
        self.assertEqual(summary["primaryOpponent"]["name"], "Mugger")
        self.assertEqual(summary["combatWindow"]["startTick"], 10)
        self.assertEqual(summary["combatWindow"]["endTick"], 18)

    def test_missing_amount_returns_null_total_with_warning(self):
        events = [combat_event(10, hitsplats=[{"eventType": "HitsplatApplied", "tick": 10, "actor": {"name": "Local player", "type": "LOCAL_PLAYER"}}])]
        summary = combat_damage_summary.analyze_data(events=events)
        self.assertIsNone(summary["damageTaken"]["total"])
        self.assertIn("combat.hitsplat.amount", summary["missingCapabilities"])

    def test_interruption_lifecycle_includes_damage_summary_fields(self):
        lifecycle = interruption_lifecycle.analyze_data(events=sample_events(), woodcutting_lifecycle={"schema": "woodcutting_lifecycle.v1", "status": "PASS"})
        self.assertEqual(lifecycle["combatDamageSummary"]["damageTakenTotal"], 3)
        self.assertEqual(lifecycle["combat"]["damageDealtTotal"], 7)

    def test_context_service_returns_compact_combat_damage_summary(self):
        combat_state = sample_events()[0]["sources"][0]["data"]
        response = context_service.build_context_response(
            {"status": {}, "baseline": {}, "candidates": [], "combat_state": combat_state},
            {"schema": context_service.REQUEST_SCHEMA, "needs": ["combat_damage_summary", "damage_taken", "damage_dealt", "primary_opponent"], "responseMode": "compact"},
        )
        self.assertIn("combatDamageSummary", response)
        self.assertEqual(response["primaryOpponent"]["name"], "Mugger")
        self.assertEqual(response["damageTaken"]["total"], 2)

    def test_task_script_api_exposes_damage_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            summary = combat_damage_summary.analyze_data(events=sample_events())
            (folder / "combat_damage_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            self.assertTrue(task_script_api.did_take_damage(folder))
            self.assertTrue(task_script_api.did_deal_damage(folder))
            self.assertEqual(task_script_api.get_primary_opponent(folder)["name"], "Mugger")


if __name__ == "__main__":
    unittest.main()
