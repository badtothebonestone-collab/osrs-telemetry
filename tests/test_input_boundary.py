from __future__ import annotations

import ast
import unittest
from pathlib import Path

import osrs_bot.arduino as arduino


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "osrs_bot"

REQUIRED_COORDINATOR_OPERATIONS = {
    "_begin_command_ledger",
    "_acquire_input_lease",
    "_command_evidence",
    "_end_command_ledger",
    "_connect",
    "_arm",
    "_input_capabilities",
    "_current_position",
    "_move_relative",
    "_assert_foreground",
    "_virtual_desktop_bounds",
    "_verify_physical_mouse_buttons_released",
    "_mouse_down",
    "_mouse_up",
    "_press",
    "_camera_hold",
    "_wheel",
    "_stop_all",
    "_disarm",
    "_firmware_status",
    "_close",
}
FORBIDDEN_RAW_CALLS = REQUIRED_COORDINATOR_OPERATIONS | {
    "_write_line",
    "_send",
    "_send_armed",
    "_legacy_configure_movement_safety",
    "_legacy_clear_movement_safety",
    "_legacy_move_to_absolute",
    "_move_relative_chunked",
    "_correct_to_endpoint",
    "_legacy_move_relative",
    "_move_to",
    "_legacy_move",
    "_legacy_click_at",
    "_legacy_move_and_click",
    "_legacy_key_down",
    "_legacy_key_up",
}
SOFTWARE_INPUT_MODULES = {
    "keyboard",
    "mouse",
    "pyautogui",
    "pydirectinput",
    "pynput",
}
GLOBAL_INPUT_HOOK_TOKENS = {
    "GetAsyncKeyState",
    "GlobalScreen",
    "RegisterRawInputDevices",
    "SetWindowsHookEx",
    "addAWTEventListener",
    "org.jnativehook",
}
DEMONSTRATION_EVIDENCE_MODULES = {
    "demonstration.py",
    "screen_capture.py",
}
DEMONSTRATION_FORBIDDEN_IMPORTS = {
    "action",
    "arduino",
    "input_coordinator",
    "login",
    "runtime",
}
CURSOR_POLICY_MODULES = {
    path.name
    for path in PACKAGE.glob("*.py")
    # The passive debug overlay is a separately bounded GUI surface whose own
    # topmost-window placement is not a production input or recovery path.
    if path.name != "debug_overlay.py"
}
FORBIDDEN_CURSOR_MUTATION_APIS = {
    "SetWindowPos",
    "MoveWindow",
    "SetCursorPos",
    "SendInput",
    "mouse_event",
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE).with_suffix("")
    return "osrs_bot." + ".".join(relative.parts)


class AutomatedInputBoundaryTests(unittest.TestCase):
    def test_only_coordinator_imports_private_arduino_transport(self) -> None:
        arduino_importers: set[str] = set()
        transport_importers: set[str] = set()
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "arduino",
                    "osrs_bot.arduino",
                }:
                    arduino_importers.add(_module_name(path))
                    if any(alias.name == "_ArduinoHIDTransport" for alias in node.names):
                        transport_importers.add(_module_name(path))
                elif isinstance(node, ast.ImportFrom) and any(
                    alias.name == "arduino" for alias in node.names
                ) and (node.module is None or node.module == "osrs_bot"):
                    arduino_importers.add(_module_name(path))
                elif isinstance(node, ast.Import):
                    if any(alias.name in {"osrs_bot.arduino", "arduino"} for alias in node.names):
                        arduino_importers.add(_module_name(path))

        self.assertEqual({"osrs_bot.input_coordinator"}, arduino_importers)
        self.assertEqual({"osrs_bot.input_coordinator"}, transport_importers)

    def test_no_production_module_bypasses_coordinator_raw_calls(self) -> None:
        offenders: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            if path.name in {"arduino.py", "input_coordinator.py"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in FORBIDDEN_RAW_CALLS:
                        offenders.append(f"{path.name}:{node.lineno}:{node.func.attr}")

        self.assertEqual([], offenders)

    def test_transport_and_raw_operations_are_not_public_api(self) -> None:
        self.assertFalse(hasattr(arduino, "ArduinoHIDBackend"))
        transport = arduino._ArduinoHIDTransport
        self.assertTrue(transport.__name__.startswith("_"))
        for name in REQUIRED_COORDINATOR_OPERATIONS:
            self.assertTrue(name.startswith("_"))
            self.assertTrue(hasattr(transport, name), name)

    def test_camera_capability_transport_remains_coordinator_only(self) -> None:
        for name in ("_input_capabilities", "_camera_hold", "_wheel"):
            offenders: list[str] = []
            for path in PACKAGE.rglob("*.py"):
                if path.name in {"arduino.py", "input_coordinator.py"}:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == name
                    ):
                        offenders.append(f"{path.name}:{node.lineno}:{name}")
            self.assertEqual([], offenders, name)

        coordinator = (PACKAGE / "input_coordinator.py").read_text(encoding="utf-8")
        self.assertIn("def execute_camera_hold(", coordinator)
        self.assertIn("def execute_camera_zoom(", coordinator)
        self.assertNotIn("def execute_key_down(", coordinator)
        self.assertNotIn("def execute_key_up(", coordinator)

    def test_no_software_input_fallback_exists_in_python_or_java(self) -> None:
        python_offenders: list[str] = []
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in SOFTWARE_INPUT_MODULES:
                            python_offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".", 1)[0] in SOFTWARE_INPUT_MODULES:
                        python_offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

        java_offenders: list[str] = []
        for path in ROOT.rglob("*.java"):
            text = path.read_text(encoding="utf-8")
            if "java.awt.Robot" in text or "new Robot(" in text:
                java_offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual([], python_offenders)
        self.assertEqual([], java_offenders)

    def test_demonstration_capture_uses_no_os_or_global_input_hook(self) -> None:
        offenders: list[str] = []
        paths = [
            *(PACKAGE / name for name in sorted(DEMONSTRATION_EVIDENCE_MODULES)),
            ROOT / "src/main/java/com/osrstelemetry/CameraInputCapture.java",
            ROOT / "src/main/java/com/osrstelemetry/TelemetryPlugin.java",
        ]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for token in sorted(GLOBAL_INPUT_HOOK_TOKENS):
                if token in source:
                    offenders.append(f"{path.relative_to(ROOT)}:{token}")

        self.assertEqual([], offenders)

    def test_demonstration_evidence_modules_have_no_input_authority(self) -> None:
        offenders: list[str] = []
        for name in sorted(DEMONSTRATION_EVIDENCE_MODULES):
            path = PACKAGE / name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {
                        alias.name.split(".", 1)[0] for alias in node.names
                    }
                elif isinstance(node, ast.ImportFrom):
                    imported = {
                        (node.module or alias.name).split(".", 1)[0]
                        for alias in node.names
                    }
                else:
                    continue
                for module in imported & (
                    DEMONSTRATION_FORBIDDEN_IMPORTS | SOFTWARE_INPUT_MODULES
                ):
                    offenders.append(f"{name}:{node.lineno}:import:{module}")
            for token in (
                "ArduinoHID",
                "InputCoordinator",
                "_ArduinoHIDTransport",
            ):
                if token in source:
                    offenders.append(f"{name}:source:{token}")

        self.assertEqual([], offenders)

    def test_production_cursor_recovery_and_focus_cannot_mutate_window_geometry_or_use_software_cursor_input(
        self,
    ) -> None:
        offenders: list[str] = []
        for name in sorted(CURSOR_POLICY_MODULES):
            path = PACKAGE / name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for api in sorted(FORBIDDEN_CURSOR_MUTATION_APIS):
                if api in source:
                    offenders.append(f"{name}:source:{api}")
            for node in ast.walk(tree):
                referenced_name = None
                if isinstance(node, ast.Attribute):
                    referenced_name = node.attr
                elif isinstance(node, ast.Name):
                    referenced_name = node.id
                if referenced_name in FORBIDDEN_CURSOR_MUTATION_APIS:
                    offenders.append(
                        f"{name}:{node.lineno}:{referenced_name}"
                    )

        # Every production Python module except the explicitly separate passive
        # debug overlay is in this cursor/window-mutation audit.
        self.assertEqual([], offenders)

    def test_login_runtime_and_actions_depend_on_coordinator_not_transport(self) -> None:
        for name in ("action.py", "login.py", "runtime.py"):
            source = (PACKAGE / name).read_text(encoding="utf-8")
            self.assertIn("InputCoordinator", source, name)
            self.assertNotIn("ArduinoHID", source, name)
            self.assertNotIn("_ArduinoHIDTransport", source, name)

    def test_state_changing_commands_never_auto_retry_after_watchdog_rejection(self) -> None:
        source = (PACKAGE / "arduino.py").read_text(encoding="utf-8")
        self.assertNotIn("recover_watchdog_disarm", source)
        self.assertNotIn("def _ensure_armed", source)
        self.assertNotIn("REJECTED_RETRYABLE", source)


if __name__ == "__main__":
    unittest.main()
