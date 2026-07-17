from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from osrs_bot.contract_limits import MAX_PRIORITY_OBJECT_IDS
from osrs_bot.definition import (
    LUMBRIDGE_SWAMP_COPPER_V1,
    LUMBRIDGE_WEST_TREES_V1,
    TaskCapability,
    TaskType,
)
from osrs_bot.task_authoring import (
    TASK_DEFINITION_SCHEMA,
    TaskDefinitionAuthoringError,
    canonical_task_definition_json,
    decode_task_definition,
    load_task_definition,
    main,
    scaffold_document,
    task_definition_document,
    task_definition_summary,
)


_EXAMPLES = Path(__file__).parents[1] / "examples" / "task_definitions"


class TaskDefinitionCodecTests(unittest.TestCase):
    def test_builtin_definitions_have_canonical_round_trips(self) -> None:
        for definition in (LUMBRIDGE_WEST_TREES_V1, LUMBRIDGE_SWAMP_COPPER_V1):
            with self.subTest(definition=definition.definition_id):
                document = task_definition_document(definition)
                decoded = decode_task_definition(document)

                self.assertEqual(definition, decoded)
                self.assertEqual(
                    canonical_task_definition_json(definition),
                    canonical_task_definition_json(decoded),
                )
                self.assertEqual(TASK_DEFINITION_SCHEMA, document["schema_version"])
                self.assertTrue(document["runnable"])

    def test_unknown_fields_are_rejected_at_their_exact_path(self) -> None:
        top_level = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        top_level["surprise"] = True
        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError, r"\$: unknown field\(s\): surprise"
        ):
            decode_task_definition(top_level)

        nested = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        nested["definition"]["resource"]["selector"]["maybe"] = "ambiguous"
        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            r"\$\.definition\.resource\.selector: unknown field\(s\): maybe",
        ):
            decode_task_definition(nested)

    def test_missing_fields_are_rejected_instead_of_defaulted(self) -> None:
        document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        del document["definition"]["recovery"]["max_target_incomplete_frames"]

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            r"recovery: missing field\(s\): max_target_incomplete_frames",
        ):
            decode_task_definition(document)

    def test_duplicate_json_object_members_are_not_silently_last_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "duplicate.json"
            source.write_text(
                '{"schema_version":"osrs_bot.task_definition.v1",'
                '"schema_version":"shadowed","runnable":true,"definition":{}}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                TaskDefinitionAuthoringError,
                "duplicate JSON object field is ambiguous: 'schema_version'",
            ):
                load_task_definition(source)

    def test_scalar_or_array_and_boolean_or_integer_ambiguity_is_rejected(self) -> None:
        scalar = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        scalar["definition"]["resource"]["selector"]["object_ids"] = 1276
        with self.assertRaisesRegex(TaskDefinitionAuthoringError, "must be a JSON array"):
            decode_task_definition(scalar)

        boolean_id = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        boolean_id["definition"]["resource"]["selector"]["object_ids"] = [True]
        with self.assertRaisesRegex(TaskDefinitionAuthoringError, "booleans are not integers"):
            decode_task_definition(boolean_id)

    def test_duplicate_set_values_and_owned_identifiers_are_rejected(self) -> None:
        duplicate_object = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        duplicate_object["definition"]["resource"]["selector"]["object_ids"] = [
            1276,
            1276,
        ]
        with self.assertRaisesRegex(TaskDefinitionAuthoringError, "must not contain duplicates"):
            decode_task_definition(duplicate_object)

        duplicate_step = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        duplicate_step["definition"]["route_to_resource"]["steps"][0]["step_id"] = (
            duplicate_step["definition"]["route_to_bank"]["steps"][0]["step_id"]
        )
        with self.assertRaisesRegex(TaskDefinitionAuthoringError, "route step IDs must be unique"):
            decode_task_definition(duplicate_step)

    def test_resource_and_bank_selectors_respect_observation_priority_id_ceiling(self) -> None:
        for component in ("resource", "bank"):
            with self.subTest(component=component, boundary="exact"):
                boundary = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
                boundary["definition"][component]["selector"]["object_ids"] = list(
                    range(1, MAX_PRIORITY_OBJECT_IDS + 1)
                )
                decoded = decode_task_definition(boundary)
                selector = (
                    decoded.resource.selector
                    if component == "resource"
                    else decoded.bank.selector
                )
                self.assertEqual(MAX_PRIORITY_OBJECT_IDS, len(selector.object_ids))

            with self.subTest(component=component, boundary="overflow"):
                overflow = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
                overflow["definition"][component]["selector"]["object_ids"] = list(
                    range(1, MAX_PRIORITY_OBJECT_IDS + 2)
                )
                with self.assertRaisesRegex(
                    TaskDefinitionAuthoringError,
                    rf"\$\.definition\.{component}\.selector\.object_ids: "
                    rf"must contain at most {MAX_PRIORITY_OBJECT_IDS} values",
                ):
                    decode_task_definition(overflow)

    def test_bad_identifier_is_rejected_before_runtime_binding(self) -> None:
        document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        document["definition"]["definition_id"] = "Bad ID / mutable alias"

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            r"definition_id: must be a lowercase identifier",
        ):
            decode_task_definition(document)

    def test_route_anchor_mismatch_is_rejected(self) -> None:
        document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        document["definition"]["route_to_bank"]["start_anchor"]["x"] += 1

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            "bank route must start at the resource anchor",
        ):
            decode_task_definition(document)

    def test_equipment_policy_requires_observation_capability(self) -> None:
        document = task_definition_document(LUMBRIDGE_SWAMP_COPPER_V1)
        document["definition"]["capabilities"].remove(
            TaskCapability.EQUIPMENT_OBSERVATION.value
        )

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            "configured equipment requires the equipment_observation capability",
        ):
            decode_task_definition(document)

    def test_known_but_unsupported_npc_capability_fails_honestly(self) -> None:
        document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        document["definition"]["capabilities"].append(
            TaskCapability.NPC_INTERACTION_GEOMETRY.value
        )
        document["definition"]["resource"]["interaction_kind"] = "npc_actor"

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            "runtime does not support capability/capabilities: npc_interaction_geometry",
        ):
            decode_task_definition(document)

    def test_future_quest_item_orchestration_fails_honestly(self) -> None:
        document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        document["definition"]["capabilities"].append(
            TaskCapability.QUEST_ITEM_ORCHESTRATION.value
        )

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            "runtime does not support capability/capabilities: "
            "quest_item_orchestration",
        ):
            decode_task_definition(document)

    def test_deposit_all_runtime_rejects_selective_retention(self) -> None:
        document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
        document["definition"]["inventory"]["allowed_item_ids"].append(1265)
        document["definition"]["inventory"]["retain_item_ids"].append(1265)

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            "selective retention is not supported by the deposit-all runtime",
        ):
            decode_task_definition(document)

    def test_runtime_incoherent_authoring_switches_fail_at_the_exact_field(self) -> None:
        cases = (
            (
                ("task_type",),
                TaskType.COMBAT.value,
                r"task_type: the current production runtime supports only gathering task definitions",
            ),
            (
                ("inventory", "minimum_free_slots"),
                0,
                r"minimum_free_slots: must be at least 1",
            ),
            (
                ("inventory", "require_only_allowed_items"),
                False,
                r"require_only_allowed_items: must be true",
            ),
            (
                ("inventory", "require_nonempty_deposit"),
                False,
                r"require_nonempty_deposit: must be true",
            ),
            (
                ("inventory", "require_produced_item_when_full"),
                False,
                r"require_produced_item_when_full: must be true",
            ),
            (
                ("verification", "deposit_expected_quantity"),
                1,
                r"deposit_expected_quantity: must be zero",
            ),
            (
                ("navigation", "allow_polyline_reconciliation"),
                False,
                r"allow_polyline_reconciliation: must be true",
            ),
            (
                ("navigation", "require_mandatory_transitions"),
                False,
                r"require_mandatory_transitions: must be true",
            ),
        )
        for path, value, message in cases:
            with self.subTest(path=path):
                document = task_definition_document(LUMBRIDGE_WEST_TREES_V1)
                target = document["definition"]
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = value
                with self.assertRaisesRegex(TaskDefinitionAuthoringError, message):
                    decode_task_definition(document)

    def test_scaffold_is_complete_shape_but_intentionally_non_runnable(self) -> None:
        scaffold = scaffold_document()
        self.assertFalse(scaffold["runnable"])
        self.assertIn("definition", scaffold)
        self.assertIn("route_to_bank", scaffold["definition"])

        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError, "intentionally non-runnable"
        ):
            decode_task_definition(scaffold)


class TaskDefinitionExampleTests(unittest.TestCase):
    def test_committed_woodcut_and_mining_examples_are_canonical(self) -> None:
        examples = (
            ("lumbridge_west_trees_v1.json", LUMBRIDGE_WEST_TREES_V1),
            ("lumbridge_swamp_copper_v1.json", LUMBRIDGE_SWAMP_COPPER_V1),
        )
        for filename, expected in examples:
            with self.subTest(filename=filename):
                path = _EXAMPLES / filename
                definition = load_task_definition(path)
                self.assertEqual(expected, definition)
                self.assertEqual(
                    canonical_task_definition_json(expected),
                    path.read_text(encoding="utf-8"),
                )

    def test_future_npc_fishing_example_documents_the_capability_boundary(self) -> None:
        path = _EXAMPLES / "unsupported_npc_fishing_v1.json"
        with self.assertRaisesRegex(
            TaskDefinitionAuthoringError,
            "runtime does not support capability/capabilities: npc_interaction_geometry",
        ):
            load_task_definition(path)


class TaskDefinitionCliTests(unittest.TestCase):
    def test_validate_and_inspect_json_commands(self) -> None:
        document = canonical_task_definition_json(LUMBRIDGE_WEST_TREES_V1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "task.json"
            source.write_text(document, encoding="utf-8")

            validate_output = io.StringIO()
            with redirect_stdout(validate_output):
                self.assertEqual(0, main(["validate", str(source), "--json"]))
            validation = json.loads(validate_output.getvalue())
            self.assertTrue(validation["valid"])
            self.assertEqual(
                LUMBRIDGE_WEST_TREES_V1.definition_id,
                validation["definition_id"],
            )
            self.assertEqual(64, len(validation["canonical_sha256"]))

            inspect_output = io.StringIO()
            with redirect_stdout(inspect_output):
                self.assertEqual(0, main(["inspect", str(source), "--json"]))
            inspected = json.loads(inspect_output.getvalue())
            self.assertEqual(task_definition_summary(LUMBRIDGE_WEST_TREES_V1), inspected)

    def test_validate_reports_structured_failure_and_scaffold_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            invalid = directory / "invalid.json"
            invalid.write_text(json.dumps(scaffold_document()), encoding="utf-8")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(2, main(["validate", str(invalid), "--json"]))
            self.assertFalse(json.loads(output.getvalue())["valid"])

            target = directory / "scaffold.json"
            target.write_text("do not replace", encoding="utf-8")
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(2, main(["scaffold", "--output", str(target)]))
            self.assertEqual("do not replace", target.read_text(encoding="utf-8"))
            self.assertIn("refusing to replace", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
