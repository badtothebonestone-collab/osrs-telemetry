from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest

import osrs_bot.definition as definition_module
from osrs_bot.definition import (
    FixedRoute,
    LUMBRIDGE_WEST_TREES_V1,
    RouteStepKind,
    TaskSiteDefinition,
)
from osrs_bot.model import WorldPoint


_FIXTURE = Path(__file__).parent / "fixtures" / "golden_lumbridge_cycle.json"


def _route_facts(route: FixedRoute) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for step in route.steps:
        item: dict[str, object] = {
            "id": step.step_id,
            "kind": step.kind.value,
            "location": [step.location.x, step.location.y, step.location.plane],
            "arrivalRadius": step.arrival_radius,
        }
        if step.kind is RouteStepKind.OBJECT:
            item.update(
                {
                    "objectId": step.object_id,
                    "name": step.object_name,
                    "action": step.action,
                    "expectedPlane": step.expected_plane,
                }
            )
        facts.append(item)
    return facts


class BuiltinDefinitionFactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    def test_exactly_one_validated_builtin_definition_exists(self) -> None:
        builtins = [
            value
            for value in vars(definition_module).values()
            if isinstance(value, TaskSiteDefinition)
        ]

        self.assertEqual([LUMBRIDGE_WEST_TREES_V1], builtins)
        self.assertEqual("lumbridge_west_trees_v1", LUMBRIDGE_WEST_TREES_V1.definition_id)
        self.assertEqual(
            "Lumbridge West ordinary Trees to Lumbridge Castle bank",
            LUMBRIDGE_WEST_TREES_V1.display_name,
        )
        self.assertEqual(1, LUMBRIDGE_WEST_TREES_V1.version)

    def test_resource_bank_inventory_and_verification_facts_are_exact(self) -> None:
        definition = LUMBRIDGE_WEST_TREES_V1
        fixture_task = self.fixture["task"]

        self.assertEqual(frozenset({1276}), definition.resource.selector.object_ids)
        self.assertEqual(fixture_task["tree"]["name"], definition.resource.selector.name)
        self.assertEqual(fixture_task["tree"]["action"], definition.resource.selector.action)
        self.assertEqual(
            frozenset({fixture_task["tree"]["producedItemId"]}),
            definition.resource.produced_item_ids,
        )
        self.assertEqual(WorldPoint(*fixture_task["treeArea"]), definition.resource.work_area.anchor)
        self.assertEqual(16, definition.resource.work_area.radius)

        self.assertEqual(frozenset({18491}), definition.bank.selector.object_ids)
        self.assertEqual(fixture_task["bank"]["name"], definition.bank.selector.name)
        self.assertEqual(fixture_task["bank"]["action"], definition.bank.selector.action)
        self.assertEqual(WorldPoint(*fixture_task["bankAnchor"]), definition.bank.anchor)
        self.assertEqual(6, definition.bank.interaction_radius)

        self.assertEqual(frozenset({1511}), definition.inventory.allowed_item_ids)
        self.assertEqual(frozenset({1511}), definition.inventory.deposit_item_ids)
        self.assertTrue(definition.inventory.require_only_allowed_items)
        self.assertTrue(definition.inventory.require_nonempty_deposit)
        self.assertTrue(definition.inventory.require_produced_item_when_full)

        self.assertEqual(8, definition.verification.action_deadline_ticks)
        self.assertEqual(100, definition.verification.resource_deadline_ticks)
        self.assertEqual(4, definition.verification.route_stable_ticks)
        self.assertEqual(0, definition.verification.deposit_expected_quantity)
        self.assertEqual("climb", definition.verification.transition_dialogue_prompt_contains)
        self.assertEqual("climb up", definition.verification.transition_up_option_contains)
        self.assertEqual("climb down", definition.verification.transition_down_option_contains)

    def test_all_runtime_route_facts_match_the_golden_fixture(self) -> None:
        definition = LUMBRIDGE_WEST_TREES_V1
        fixture_task = self.fixture["task"]
        expected_return = [
            {key: value for key, value in item.items() if key != "arrivalOnly"}
            for item in fixture_task["routeToTrees"]
        ]

        self.assertEqual(fixture_task["routeToBank"], _route_facts(definition.route_to_bank))
        self.assertEqual(expected_return, _route_facts(definition.route_to_resource))
        self.assertEqual(
            definition.resource.work_area.anchor,
            definition.route_to_bank.start_anchor,
        )
        self.assertEqual(definition.bank.anchor, definition.route_to_bank.destination_anchor)
        self.assertEqual(definition.bank.anchor, definition.route_to_resource.start_anchor)
        self.assertEqual(
            definition.resource.work_area.anchor,
            definition.route_to_resource.destination_anchor,
        )
        object_steps = [
            step
            for route in (definition.route_to_bank, definition.route_to_resource)
            for step in route.steps
            if step.kind is RouteStepKind.OBJECT
        ]
        self.assertEqual(3, len(object_steps))
        self.assertTrue(all(step.allowed_actions == (step.action, "Climb") for step in object_steps))
        first_walk = definition.route_to_bank.steps[0]
        self.assertTrue(first_walk.is_walk)
        self.assertEqual("route:west_approach_bridge", first_walk.target_key)
        self.assertTrue(all(not step.is_walk for step in object_steps))

    def test_definition_provenance_matches_the_corrected_golden_fixture(self) -> None:
        provenance = LUMBRIDGE_WEST_TREES_V1.provenance
        fixture = self.fixture
        fixture_provenance = fixture["provenance"]

        self.assertEqual(fixture["schema"], provenance.fixture_schema)
        self.assertEqual(fixture["id"], provenance.fixture_id)
        self.assertEqual(fixture["description"], provenance.description)
        self.assertEqual(fixture_provenance["evidenceDate"], provenance.evidence_date)
        self.assertEqual(fixture_provenance["baselineParent"], provenance.baseline_parent)
        self.assertEqual(fixture_provenance["proofRoot"], provenance.proof_root)
        self.assertEqual(fixture_provenance["evidenceKind"], provenance.evidence_kind)
        self.assertEqual(fixture_provenance["limitations"], provenance.limitations)
        self.assertEqual(
            fixture_provenance["evidence"],
            [
                {"path": item.path, "sha256": item.sha256, "proves": item.proves}
                for item in provenance.evidence
            ],
        )

    def test_shared_engine_layers_do_not_import_site_or_profile_data(self) -> None:
        root = Path(__file__).parents[1] / "osrs_bot"
        for module_name in (
            "model.py",
            "observation.py",
            "safety.py",
            "verification.py",
            "runtime.py",
            "task_contract.py",
            "configuration.py",
        ):
            source = (root / module_name).read_text(encoding="utf-8")
            imports = [
                node.module
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.ImportFrom)
            ]
            with self.subTest(module=module_name):
                self.assertNotIn("definition", imports)
                self.assertNotIn("profile", imports)
                self.assertNotIn("osrs_bot.definition", imports)
                self.assertNotIn("osrs_bot.profile", imports)


class DefinitionValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = LUMBRIDGE_WEST_TREES_V1

    def test_definition_graph_is_frozen_and_uses_immutable_shapes(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.definition.version = 2  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            self.definition.route_to_bank.steps[0].action = "Go"  # type: ignore[misc]

        self.assertIs(type(self.definition.route_to_bank.steps), tuple)
        self.assertIs(type(self.definition.resource.selector.object_ids), frozenset)
        self.assertIs(type(self.definition.inventory.allowed_item_ids), frozenset)
        self.assertIs(type(self.definition.provenance.evidence), tuple)
        self.assertFalse(hasattr(self.definition, "__dict__"))
        self.assertFalse(hasattr(self.definition.resource.work_area.anchor, "__dict__"))

    def test_mutable_collection_shapes_are_rejected(self) -> None:
        selector = self.definition.resource.selector
        object_step = self.definition.route_to_bank.steps[13]
        invalid_builders = (
            lambda: replace(selector, object_ids={1276}),
            lambda: replace(
                self.definition.inventory,
                allowed_item_ids={1511},
            ),
            lambda: replace(
                self.definition.route_to_bank,
                steps=list(self.definition.route_to_bank.steps),
            ),
            lambda: replace(
                self.definition.provenance,
                evidence=list(self.definition.provenance.evidence),
            ),
            lambda: replace(object_step, alternate_actions=["Climb"]),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()

    def test_bool_and_nonpositive_ids_radii_versions_and_deadlines_are_rejected(self) -> None:
        resource_selector = self.definition.resource.selector
        object_step = self.definition.route_to_bank.steps[13]
        invalid_builders = (
            lambda: replace(self.definition, version=True),
            lambda: replace(self.definition, version=0),
            lambda: replace(resource_selector, object_ids=frozenset({True})),
            lambda: replace(resource_selector, object_ids=frozenset({0})),
            lambda: replace(
                self.definition.resource,
                produced_item_ids=frozenset({False}),
            ),
            lambda: replace(
                self.definition.resource,
                produced_item_ids=frozenset({-1}),
            ),
            lambda: replace(self.definition.resource.work_area, radius=True),
            lambda: replace(self.definition.resource.work_area, radius=0),
            lambda: replace(self.definition.bank, interaction_radius=False),
            lambda: replace(self.definition.bank, interaction_radius=-1),
            lambda: replace(object_step, object_id=True),
            lambda: replace(object_step, object_id=0),
            lambda: replace(object_step, arrival_radius=False),
            lambda: replace(object_step, arrival_radius=0),
            lambda: replace(self.definition.verification, action_deadline_ticks=True),
            lambda: replace(self.definition.verification, action_deadline_ticks=0),
            lambda: replace(self.definition.verification, resource_deadline_ticks=-1),
            lambda: replace(self.definition.verification, route_stable_ticks=False),
            lambda: replace(self.definition.verification, deposit_expected_quantity=-1),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()

    def test_blank_and_duplicate_identifiers_are_rejected(self) -> None:
        route = self.definition.route_to_bank
        first, second = route.steps[:2]
        duplicate_within_route = (first, replace(second, step_id=first.step_id), *route.steps[2:])
        duplicate_across_route = replace(
            self.definition.route_to_resource,
            steps=(
                replace(
                    self.definition.route_to_resource.steps[0],
                    step_id=first.step_id,
                ),
                *self.definition.route_to_resource.steps[1:],
            ),
        )
        invalid_builders = (
            lambda: replace(self.definition, definition_id=" "),
            lambda: replace(self.definition, display_name=""),
            lambda: replace(route, route_id=""),
            lambda: replace(first, step_id="  "),
            lambda: replace(route, steps=duplicate_within_route),
            lambda: replace(
                self.definition,
                route_to_resource=replace(
                    self.definition.route_to_resource,
                    route_id=route.route_id,
                ),
            ),
            lambda: replace(self.definition, route_to_resource=duplicate_across_route),
            lambda: replace(
                self.definition.provenance,
                evidence=(
                    self.definition.provenance.evidence[0],
                    self.definition.provenance.evidence[0],
                ),
            ),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()

    def test_invalid_planes_and_incoherent_transitions_are_rejected(self) -> None:
        route = self.definition.route_to_bank
        walk_step = route.steps[0]
        transition = route.steps[13]
        wrong_transition = replace(transition, expected_plane=3)
        invalid_builders = (
            lambda: replace(
                walk_step,
                location=WorldPoint(walk_step.location.x, walk_step.location.y, True),
            ),
            lambda: replace(
                walk_step,
                location=WorldPoint(walk_step.location.x, walk_step.location.y, -1),
            ),
            lambda: replace(
                walk_step,
                location=WorldPoint(walk_step.location.x, walk_step.location.y, 4),
            ),
            lambda: replace(transition, expected_plane=transition.location.plane),
            lambda: replace(transition, expected_plane=False),
            lambda: replace(
                route,
                steps=(*route.steps[:13], wrong_transition, *route.steps[14:]),
            ),
            lambda: replace(
                route,
                start_anchor=WorldPoint(
                    route.start_anchor.x,
                    route.start_anchor.y,
                    1,
                ),
            ),
            lambda: replace(
                route,
                destination_anchor=WorldPoint(
                    route.destination_anchor.x + 1,
                    route.destination_anchor.y,
                    route.destination_anchor.plane,
                ),
            ),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()

    def test_walk_and_object_transition_shapes_are_fail_closed(self) -> None:
        walk_step = self.definition.route_to_bank.steps[0]
        object_step = self.definition.route_to_bank.steps[13]
        invalid_builders = (
            lambda: replace(walk_step, action="walk here"),
            lambda: replace(walk_step, object_id=123),
            lambda: replace(walk_step, expected_plane=1),
            lambda: replace(object_step, object_id=None),
            lambda: replace(object_step, object_name=" "),
            lambda: replace(object_step, expected_plane=None),
            lambda: replace(object_step, alternate_actions=("Climb", "Climb")),
            lambda: replace(object_step, kind="object"),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()

    def test_produced_items_cannot_be_excluded_from_inventory_or_deposit_predicates(self) -> None:
        produced_excluded_from_all = replace(
            self.definition.inventory,
            allowed_item_ids=frozenset({995}),
            deposit_item_ids=frozenset({995}),
        )
        produced_excluded_from_deposit = replace(
            self.definition.inventory,
            allowed_item_ids=frozenset({995, 1511}),
            deposit_item_ids=frozenset({995}),
        )

        for inventory in (produced_excluded_from_all, produced_excluded_from_deposit):
            with self.subTest(inventory=inventory):
                with self.assertRaises(ValueError):
                    replace(self.definition, inventory=inventory)

    def test_definition_rejects_incoherent_route_anchors(self) -> None:
        shifted_tree_anchor = WorldPoint(3195, 3240, 0)
        shifted_bank_anchor = WorldPoint(3209, 3221, 2)
        invalid_builders = (
            lambda: replace(
                self.definition,
                resource=replace(
                    self.definition.resource,
                    work_area=replace(
                        self.definition.resource.work_area,
                        anchor=shifted_tree_anchor,
                    ),
                ),
            ),
            lambda: replace(
                self.definition,
                bank=replace(self.definition.bank, anchor=shifted_bank_anchor),
            ),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()


if __name__ == "__main__":
    unittest.main()
