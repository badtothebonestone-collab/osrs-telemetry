from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
import unittest

from osrs_bot.definition import LUMBRIDGE_SWAMP_COPPER_V1, LUMBRIDGE_WEST_TREES_V1
from osrs_bot.profile import (
    BoundProfile,
    DEFAULT_BINDING,
    DEFAULT_PROFILE,
    Profile,
    bind_builtin_profile,
)


class ProfileTests(unittest.TestCase):
    def test_default_profile_binds_to_the_default_builtin_definition(self) -> None:
        bound = bind_builtin_profile(DEFAULT_PROFILE)

        self.assertIs(bound.definition, LUMBRIDGE_WEST_TREES_V1)
        self.assertIs(bound.profile, DEFAULT_PROFILE)
        self.assertEqual(DEFAULT_PROFILE.cycle_goal, 1)
        self.assertEqual(DEFAULT_BINDING, bound)

    def test_contract_contains_only_profile_choices(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(Profile)),
            (
                "profile_id",
                "definition_id",
                "cycle_goal",
                "item_quantity_goal",
                "inventories_banked_goal",
                "duration_seconds",
                "start_at_utc",
                "stop_at_utc",
                "stop_when_inventory_full",
                "max_actions",
                "reconcile_on_start",
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            DEFAULT_PROFILE.cycle_goal = 2  # type: ignore[misc]
        self.assertFalse(hasattr(DEFAULT_PROFILE, "__dict__"))
        self.assertFalse(hasattr(DEFAULT_BINDING, "__dict__"))

    def test_profile_rejects_malformed_values(self) -> None:
        valid = dict(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=1,
        )
        for field_name, value in (
            ("profile_id", ""),
            ("profile_id", "bad profile"),
            ("definition_id", "UPPERCASE"),
            ("cycle_goal", 0),
            ("cycle_goal", True),
        ):
            values = dict(valid)
            values[field_name] = value
            with self.subTest(field_name=field_name, value=value):
                with self.assertRaisesRegex(ValueError, field_name):
                    Profile(**values)

    def test_profile_id_is_validated_metadata_not_a_definition_choice(self) -> None:
        profile = Profile(
            profile_id="operator_named_profile",
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=1,
        )
        bound = bind_builtin_profile(profile)

        self.assertIs(bound.profile, profile)
        self.assertIs(bound.definition, LUMBRIDGE_WEST_TREES_V1)

    def test_binding_rejects_unknown_definition(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id="unknown_definition_v1",
            cycle_goal=1,
        )
        with self.assertRaisesRegex(ValueError, "unsupported definition_id"):
            bind_builtin_profile(profile)

    def test_binding_accepts_bounded_repeat_and_composed_goals(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=2,
            item_quantity_goal=10,
            duration_seconds=60.0,
        )
        bound = bind_builtin_profile(profile)

        self.assertEqual(2, bound.profile.cycle_goal)
        self.assertEqual(10, bound.profile.item_quantity_goal)
        self.assertEqual(60.0, bound.profile.duration_seconds)

    def test_second_builtin_definition_binds_through_the_same_contract(self) -> None:
        profile = Profile(
            profile_id="lumbridge_copper_profile",
            definition_id=LUMBRIDGE_SWAMP_COPPER_V1.definition_id,
            cycle_goal=1,
        )

        bound = bind_builtin_profile(profile)

        self.assertIs(LUMBRIDGE_SWAMP_COPPER_V1, bound.definition)

    def test_bound_profile_rejects_a_mismatched_definition(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id="different_definition_v1",
            cycle_goal=1,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            BoundProfile(profile=profile, definition=LUMBRIDGE_WEST_TREES_V1)

    def test_bound_profile_cannot_bypass_bounded_goal_validation(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=101,
        )
        with self.assertRaisesRegex(ValueError, "no greater than 100"):
            BoundProfile(profile=profile, definition=LUMBRIDGE_WEST_TREES_V1)

    def test_profile_requires_timezone_aware_utc_schedule(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            Profile(
                profile_id=DEFAULT_PROFILE.profile_id,
                definition_id=DEFAULT_PROFILE.definition_id,
                cycle_goal=1,
                start_at_utc=datetime(2026, 7, 16, 12, 0),
            )
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=1,
            start_at_utc=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(bind_builtin_profile(profile))


if __name__ == "__main__":
    unittest.main()
