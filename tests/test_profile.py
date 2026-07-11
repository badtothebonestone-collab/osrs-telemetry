from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import unittest

from osrs_bot.definition import LUMBRIDGE_WEST_TREES_V1
from osrs_bot.profile import (
    BoundProfile,
    DEFAULT_BINDING,
    DEFAULT_PROFILE,
    Profile,
    bind_builtin_profile,
)


class ProfileTests(unittest.TestCase):
    def test_default_profile_binds_to_the_only_builtin_definition(self) -> None:
        bound = bind_builtin_profile(DEFAULT_PROFILE)

        self.assertIs(bound.definition, LUMBRIDGE_WEST_TREES_V1)
        self.assertIs(bound.profile, DEFAULT_PROFILE)
        self.assertEqual(DEFAULT_PROFILE.cycle_goal, 1)
        self.assertEqual(DEFAULT_BINDING, bound)

    def test_contract_contains_only_profile_choices(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(Profile)),
            ("profile_id", "definition_id", "cycle_goal"),
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

    def test_binding_rejects_unsupported_goal(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=2,
        )
        with self.assertRaisesRegex(ValueError, "requires exactly 1"):
            bind_builtin_profile(profile)

    def test_bound_profile_rejects_a_mismatched_definition(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id="different_definition_v1",
            cycle_goal=1,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            BoundProfile(profile=profile, definition=LUMBRIDGE_WEST_TREES_V1)

    def test_bound_profile_cannot_bypass_supported_goal_validation(self) -> None:
        profile = Profile(
            profile_id=DEFAULT_PROFILE.profile_id,
            definition_id=DEFAULT_PROFILE.definition_id,
            cycle_goal=2,
        )
        with self.assertRaisesRegex(ValueError, "requires exactly 1"):
            BoundProfile(profile=profile, definition=LUMBRIDGE_WEST_TREES_V1)


if __name__ == "__main__":
    unittest.main()
