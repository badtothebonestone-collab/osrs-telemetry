from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from osrs_bot.input_capabilities import (
    CapabilityValidation,
    InputCapabilities,
    InputCapabilityError,
    InputOperation,
    RequiredInputCapabilities,
)


def _v1_identity() -> dict[str, object]:
    return {
        "name": "ArduinoHIDBridge",
        "version": "1.0.0",
        "board": "leonardo",
        "protocol": "arduino_hid.v1",
    }


def _v1_caps() -> dict[str, object]:
    return {
        "mouse": True,
        "keyboard": True,
        "relativeMove": True,
        "buttons": "left,right,middle",
        "keys": "basic",
        "holdKeys": True,
        "watchdog": True,
        "stopAll": True,
        "resetSafe": True,
    }


def _v2_identity() -> dict[str, object]:
    return {
        "name": "ArduinoHIDBridge",
        "version": "2.0.0",
        "board": "leonardo",
        "protocol": "arduino_hid.v2",
    }


def _v2_caps() -> dict[str, object]:
    return {
        "schema": "input_capabilities.v2",
        "protocol": "arduino_hid.v2",
        "firmwareVersion": "2.0.0",
        "pointer": True,
        "mouse": True,
        "relativeMove": True,
        "maxMoveDelta": 20,
        "buttons": "left,right,middle",
        "buttonDownUp": True,
        "click": True,
        "maxClickHoldMs": 250,
        "keyboard": True,
        "keys": "basic",
        "keyPress": True,
        "maxKeyPressMs": 250,
        "holdKeys": True,
        "maxHoldKeysMs": 250,
        "cameraKeyHold": True,
        "cameraKeys": "left,right,up,down",
        "maxCameraHoldMs": 600,
        "wheel": True,
        "maxWheelStep": 3,
        "arm": True,
        "watchdog": True,
        "watchdogMs": 1000,
        "stopAll": True,
        "disarm": True,
        "status": True,
        "resetSafe": True,
    }


def _status(*, watchdog_ms: int = 1000) -> dict[str, object]:
    return {
        "armed": False,
        # The production line parser normalizes literal 0/1 to bool.
        "keysDown": False,
        "mouseButtonsDown": False,
        "lastCommandAgeMs": 7,
        "watchdogMs": watchdog_ms,
    }


def _v1() -> InputCapabilities:
    return InputCapabilities.from_negotiation(
        _v1_identity(), _v1_caps(), _status()
    )


def _v2() -> InputCapabilities:
    return InputCapabilities.from_negotiation(
        _v2_identity(), _v2_caps(), _status()
    )


class InputCapabilitiesNegotiationTests(unittest.TestCase):
    def test_legacy_profile_preserves_pointer_and_short_key_only(self) -> None:
        negotiated = _v1()

        self.assertEqual("input_capabilities.v1", negotiated.schema_version)
        self.assertEqual("arduino_hid.v1", negotiated.protocol_version)
        self.assertEqual("1.0.0", negotiated.firmware_version)
        self.assertTrue(negotiated.pointer)
        self.assertTrue(negotiated.relative_move)
        self.assertEqual(20, negotiated.max_move_delta)
        self.assertTrue(negotiated.button_down_up)
        self.assertTrue(negotiated.key_press)
        self.assertEqual(250, negotiated.max_key_press_ms)
        self.assertEqual("basic", negotiated.key_set)
        self.assertTrue(negotiated.hold_keys)
        self.assertEqual(250, negotiated.max_hold_keys_ms)
        self.assertFalse(negotiated.camera_key_hold)
        self.assertEqual(frozenset(), negotiated.camera_keys)
        self.assertEqual(0, negotiated.max_camera_hold_ms)
        self.assertFalse(negotiated.wheel)
        self.assertEqual(0, negotiated.max_wheel_step)
        self.assertIsNone(RequiredInputCapabilities.pointer_move().missing_reason(negotiated))
        self.assertIsNone(
            RequiredInputCapabilities.generic_key_press(250).missing_reason(negotiated)
        )
        self.assertEqual(
            "missing_capability:camera_key_hold",
            RequiredInputCapabilities.camera_hold("left", 250).missing_reason(
                negotiated
            ),
        )
        self.assertEqual(
            "missing_capability:wheel",
            RequiredInputCapabilities.camera_zoom(1).missing_reason(negotiated),
        )

    def test_v2_exact_mapping_is_immutable_and_serializes_stably(self) -> None:
        negotiated = _v2()

        self.assertEqual("input_capabilities.v2", negotiated.schema_version)
        self.assertEqual("arduino_hid.v2", negotiated.protocol_version)
        self.assertEqual(600, negotiated.max_camera_hold_ms)
        self.assertEqual(3, negotiated.max_wheel_step)
        self.assertEqual("basic", negotiated.key_set)
        self.assertTrue(negotiated.hold_keys)
        self.assertEqual(250, negotiated.max_hold_keys_ms)
        self.assertEqual(
            frozenset({"left", "right", "up", "down"}), negotiated.camera_keys
        )
        self.assertEqual(
            ["left", "right", "up", "down"],
            negotiated.to_dict()["cameraKeys"],
        )
        self.assertEqual(
            ["left", "right", "middle"], negotiated.to_dict()["buttons"]
        )
        with self.assertRaises(FrozenInstanceError):
            negotiated.max_camera_hold_ms = 601  # type: ignore[misc]
        self.assertFalse(hasattr(negotiated, "__dict__"))

    def test_rejects_unsupported_or_malformed_identity(self) -> None:
        cases = (
            ({**_v2_identity(), "protocol": "arduino_hid.v3"}, "unsupported"),
            ({**_v2_identity(), "version": "2"}, "semantic version"),
            ({key: value for key, value in _v2_identity().items() if key != "version"}, "version"),
        )
        for identity, expected in cases:
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(InputCapabilityError, expected):
                    InputCapabilities.from_negotiation(
                        identity, _v2_caps(), _status()
                    )

    def test_v2_rejects_schema_protocol_firmware_and_watchdog_disagreement(self) -> None:
        cases = (
            ({"schema": "input_capabilities.v1"}, _status(), "schema"),
            ({"protocol": "arduino_hid.v1"}, _status(), "protocol mismatch"),
            ({"firmwareVersion": "2.0.1"}, _status(), "firmware mismatch"),
            ({"watchdogMs": 999}, _status(), "watchdog mismatch"),
            ({}, _status(watchdog_ms=999), "watchdog mismatch"),
        )
        for overrides, status, expected in cases:
            caps = {**_v2_caps(), **overrides}
            with self.subTest(overrides=overrides, status=status):
                with self.assertRaisesRegex(InputCapabilityError, expected):
                    InputCapabilities.from_negotiation(
                        _v2_identity(), caps, status
                    )

    def test_v2_requires_every_numeric_limit(self) -> None:
        for key in (
            "maxMoveDelta",
            "maxClickHoldMs",
            "maxKeyPressMs",
            "maxHoldKeysMs",
            "maxCameraHoldMs",
            "maxWheelStep",
            "watchdogMs",
        ):
            caps = _v2_caps()
            del caps[key]
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    InputCapabilityError, f"missing capability field: {key}"
                ):
                    InputCapabilities.from_negotiation(
                        _v2_identity(), caps, _status()
                    )

    def test_v2_rejects_non_integer_limits_and_non_boolean_support(self) -> None:
        malformed = (
            ({"maxCameraHoldMs": "600"}, "must be an integer"),
            ({"maxWheelStep": True}, "must be an integer"),
            ({"wheel": 1}, "must be bool"),
            ({"cameraKeyHold": "true"}, "must be bool"),
            ({"holdKeys": 1}, "must be bool"),
            ({"keys": "raw"}, "must be 'basic'"),
        )
        for overrides, expected in malformed:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(InputCapabilityError, expected):
                    InputCapabilities.from_negotiation(
                        _v2_identity(), {**_v2_caps(), **overrides}, _status()
                    )

    def test_v2_rejects_camera_hold_at_or_beyond_watchdog(self) -> None:
        caps = {**_v2_caps(), "watchdogMs": 600}
        with self.assertRaisesRegex(InputCapabilityError, "strictly below"):
            InputCapabilities.from_negotiation(
                _v2_identity(), caps, _status(watchdog_ms=600)
            )

    def test_rejects_malformed_capability_sets_and_status(self) -> None:
        bad_sets = (
            ({"buttons": "left,left,right"}, "duplicates"),
            ({"buttons": "left,right,side"}, "unsupported value"),
            ({"cameraKeys": "left,right,enter"}, "unsupported value"),
        )
        for overrides, expected in bad_sets:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(InputCapabilityError, expected):
                    InputCapabilities.from_negotiation(
                        _v2_identity(), {**_v2_caps(), **overrides}, _status()
                    )

        for key, value in (("armed", 0), ("keysDown", "0"), ("watchdogMs", True)):
            status = {**_status(), key: value}
            with self.subTest(status_key=key):
                with self.assertRaises(InputCapabilityError):
                    InputCapabilities.from_negotiation(
                        _v2_identity(), _v2_caps(), status
                    )


class RequiredInputCapabilitiesTests(unittest.TestCase):
    def test_all_v2_operations_validate(self) -> None:
        negotiated = _v2()
        requirements = (
            RequiredInputCapabilities.pointer_move(),
            RequiredInputCapabilities.pointer_click(button="left", hold_ms=50),
            RequiredInputCapabilities.generic_key_press(250),
            RequiredInputCapabilities.camera_hold("right", 600),
            RequiredInputCapabilities.camera_zoom(-3),
            RequiredInputCapabilities.cleanup(),
        )

        for requirement in requirements:
            with self.subTest(operation=requirement.operation):
                self.assertIsNone(requirement.missing_reason(negotiated))
                result = requirement.validate(negotiated)
                self.assertIsInstance(result, CapabilityValidation)
                self.assertTrue(result.allowed)
                self.assertEqual(
                    "required_input_capabilities_satisfied", result.reason
                )
                requirement.require(negotiated)

    def test_required_shapes_and_serialization_are_typed(self) -> None:
        requirement = RequiredInputCapabilities.camera_hold("up", 425)

        self.assertEqual(InputOperation.CAMERA_KEY_HOLD, requirement.operation)
        self.assertEqual(
            {
                "schema": "required_input_capabilities.v1",
                "operation": "camera_key_hold",
                "cameraDirection": "up",
                "cameraHoldMs": 425,
            },
            requirement.to_dict(),
        )
        with self.assertRaises(FrozenInstanceError):
            requirement.camera_hold_ms = 1  # type: ignore[misc]
        with self.assertRaisesRegex(InputCapabilityError, "requires exactly"):
            RequiredInputCapabilities(
                InputOperation.POINTER_MOVE, wheel_amount=1
            )

    def test_camera_directions_and_bounds_reject_before_negotiation(self) -> None:
        for direction in ("enter", "LEFT", "", "left right"):
            with self.subTest(direction=direction):
                with self.assertRaisesRegex(InputCapabilityError, "camera_direction"):
                    RequiredInputCapabilities.camera_hold(direction, 100)
        for duration in (-1, 0, 601, True):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(InputCapabilityError, "camera_hold_ms"):
                    RequiredInputCapabilities.camera_hold("left", duration)  # type: ignore[arg-type]

    def test_wheel_is_nonzero_signed_and_bounded_before_negotiation(self) -> None:
        for amount in (-4, 0, 4, True):
            with self.subTest(amount=amount):
                with self.assertRaisesRegex(InputCapabilityError, "wheel_amount"):
                    RequiredInputCapabilities.camera_zoom(amount)  # type: ignore[arg-type]
        self.assertEqual(-3, RequiredInputCapabilities.camera_zoom(-3).wheel_amount)
        self.assertEqual(3, RequiredInputCapabilities.camera_zoom(3).wheel_amount)

    def test_generic_and_pointer_short_holds_remain_bounded(self) -> None:
        for constructor in (
            lambda value: RequiredInputCapabilities.generic_key_press(value),
            lambda value: RequiredInputCapabilities.pointer_click(hold_ms=value),
        ):
            self.assertIsNone(constructor(250).missing_reason(_v2()))
            with self.assertRaises(InputCapabilityError):
                constructor(251)

    def test_negotiated_limits_produce_deterministic_reasons(self) -> None:
        negotiated = _v2()
        smaller = replace(
            negotiated,
            max_click_hold_ms=40,
            max_key_press_ms=100,
            max_camera_hold_ms=300,
            max_wheel_step=2,
        )
        cases = (
            (
                RequiredInputCapabilities.pointer_click(hold_ms=41),
                "click_hold_exceeds_negotiated_max:requested=41,max=40",
            ),
            (
                RequiredInputCapabilities.generic_key_press(101),
                "key_press_exceeds_negotiated_max:requested=101,max=100",
            ),
            (
                RequiredInputCapabilities.camera_hold("left", 301),
                "camera_hold_exceeds_negotiated_max:requested=301,max=300",
            ),
            (
                RequiredInputCapabilities.camera_zoom(-3),
                "wheel_amount_exceeds_negotiated_max:requested=-3,max=2",
            ),
        )
        for requirement, expected in cases:
            with self.subTest(operation=requirement.operation):
                self.assertEqual(expected, requirement.missing_reason(smaller))
                result = requirement.validate(smaller)
                self.assertFalse(result.allowed)
                self.assertEqual(expected, result.reason)
                with self.assertRaisesRegex(
                    InputCapabilityError, expected.replace("-", r"\-")
                ):
                    requirement.require(smaller)

    def test_missing_capabilities_have_stable_pre_activation_order(self) -> None:
        negotiated = _v2()
        unsafe_envelope = replace(negotiated, arm=False)
        self.assertEqual(
            "missing_capability:arm",
            RequiredInputCapabilities.camera_zoom(1).missing_reason(
                unsafe_envelope
            ),
        )

        no_camera = replace(
            negotiated,
            camera_key_hold=False,
            camera_keys=frozenset(),
            max_camera_hold_ms=0,
        )
        self.assertEqual(
            "missing_capability:camera_key_hold",
            RequiredInputCapabilities.camera_hold("left", 1).missing_reason(
                no_camera
            ),
        )

        no_cleanup = replace(negotiated, stop_all=False, disarm=False)
        self.assertEqual(
            "missing_capability:stop_all",
            RequiredInputCapabilities.cleanup().missing_reason(no_cleanup),
        )

    def test_production_click_requirement_does_not_add_middle_button(self) -> None:
        with self.assertRaisesRegex(InputCapabilityError, "left or right"):
            RequiredInputCapabilities.pointer_click(button="middle")


if __name__ == "__main__":
    unittest.main()
