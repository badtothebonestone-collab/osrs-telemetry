from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import math
import unittest

from osrs_bot.configuration import (
    DEFAULT_RUNTIME_CONFIG,
    MAX_ACTIONS,
    MAX_OBSERVATIONS,
    MAX_POLL_SECONDS,
    MAX_REQUEST_TIMEOUT_SECONDS,
    MAX_RUNTIME_SECONDS,
    MAX_VERIFICATION_TIMEOUT_SECONDS,
    RuntimeConfig,
)


class RuntimeConfigurationTests(unittest.TestCase):
    def test_defaults_match_the_bounded_runtime_contract(self) -> None:
        config = RuntimeConfig()

        self.assertEqual(config.endpoint, "http://127.0.0.1:8893")
        self.assertEqual(config.request_timeout_seconds, 3.0)
        self.assertEqual(config.poll_seconds, 0.25)
        self.assertEqual(config.max_observations, 4_800)
        self.assertEqual(config.max_actions, 80)
        self.assertEqual(config.max_runtime_seconds, 1_200.0)
        self.assertEqual(config.verification_timeout_seconds, 75.0)
        self.assertIsNone(config.arduino_port)
        self.assertIs(config.validated_for_mode(execute=False), config)
        self.assertEqual(DEFAULT_RUNTIME_CONFIG, config)

    def test_contract_has_only_machine_and_runtime_settings(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(RuntimeConfig)),
            (
                "endpoint",
                "auth_token",
                "request_timeout_seconds",
                "arduino_port",
                "poll_seconds",
                "max_observations",
                "max_actions",
                "max_runtime_seconds",
                "verification_timeout_seconds",
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            RuntimeConfig().max_actions = 1  # type: ignore[misc]
        self.assertFalse(hasattr(DEFAULT_RUNTIME_CONFIG, "__dict__"))

    def test_execute_mode_requires_an_arduino_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "execute mode requires arduino_port"):
            RuntimeConfig().validated_for_mode(execute=True)

        config = RuntimeConfig(arduino_port="COM6")
        self.assertIs(config.validated_for_mode(execute=True), config)

    def test_endpoint_must_be_a_plain_http_origin_with_explicit_port(self) -> None:
        for endpoint in (
            "",
            "127.0.0.1:8893",
            "ftp://127.0.0.1:8893",
            "http://127.0.0.1",
            "http://user:secret@127.0.0.1:8893",
            "http://127.0.0.1:8893/snapshot",
            "http://127.0.0.1:8893?query=yes",
            "http://127.0.0.1:\n8893",
            "http://bad host:8893",
            "http://evil\\host:8893",
            "http://bad_host:8893",
            "http://two..labels:8893",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ValueError, "endpoint"):
                    RuntimeConfig(endpoint=endpoint)

        self.assertEqual(
            "http://localhost:8893",
            RuntimeConfig(endpoint="http://localhost:8893").endpoint,
        )
        self.assertEqual(
            "http://[::1]:8893",
            RuntimeConfig(endpoint="http://[::1]:8893").endpoint,
        )

    def test_optional_token_and_port_must_be_safe_trimmed_text(self) -> None:
        for field_name, value in (
            ("auth_token", ""),
            ("auth_token", " token"),
            ("auth_token", "token\r\nheader"),
            ("arduino_port", ""),
            ("arduino_port", " COM6"),
            ("arduino_port", "COM6\n"),
        ):
            with self.subTest(field_name=field_name, value=value):
                with self.assertRaisesRegex(ValueError, field_name):
                    RuntimeConfig(**{field_name: value})

        self.assertNotIn("secret-token", repr(RuntimeConfig(auth_token="secret-token")))

    def test_all_numeric_settings_reject_nonfinite_zero_negative_and_bool(self) -> None:
        numeric_fields = (
            "request_timeout_seconds",
            "poll_seconds",
            "max_runtime_seconds",
            "verification_timeout_seconds",
        )
        for field_name in numeric_fields:
            for value in (0, -1, math.inf, -math.inf, math.nan, True):
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, field_name):
                        RuntimeConfig(**{field_name: value})

        for field_name in ("max_observations", "max_actions"):
            for value in (0, -1, 1.5, True):
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, field_name):
                        RuntimeConfig(**{field_name: value})

    def test_hard_caps_are_enforced(self) -> None:
        invalid = (
            ("request_timeout_seconds", MAX_REQUEST_TIMEOUT_SECONDS + 0.01),
            ("poll_seconds", MAX_POLL_SECONDS + 0.01),
            ("max_observations", MAX_OBSERVATIONS + 1),
            ("max_actions", MAX_ACTIONS + 1),
            ("max_runtime_seconds", MAX_RUNTIME_SECONDS + 0.01),
            ("verification_timeout_seconds", MAX_VERIFICATION_TIMEOUT_SECONDS + 0.01),
        )
        for field_name, value in invalid:
            with self.subTest(field_name=field_name, value=value):
                with self.assertRaisesRegex(ValueError, field_name):
                    RuntimeConfig(**{field_name: value})

    def test_verification_timeout_cannot_exceed_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            RuntimeConfig(max_runtime_seconds=10, verification_timeout_seconds=11)


if __name__ == "__main__":
    unittest.main()
