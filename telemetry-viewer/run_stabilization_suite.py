from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


COMMANDS = [
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\capabilities.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\mission_presets.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\runtime_control.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\task_policy.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\task_state.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\__init__.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\live_state.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\inventory_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\target_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\navigation_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\navigation_intent_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\pathing_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\activity_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\intent_overlay_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\brain_context_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\service_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\bank_ui_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\bank_operation_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\return_to_resource_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\resource_return_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\post_bank_reacquisition_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\close_bank_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\analyzers\\process_inventory_analyzer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\resource_progress.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\brain_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\intent_stabilizer.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\cycle_history.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_core_daemon.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\control_live_daemon.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\mission_snapshot.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_task_policy.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_task_transition.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_navigation_intent.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_pathing_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_pathing_matrix.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_service_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_bank_ui_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_bank_operation_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_return_to_resource_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_resource_return_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_post_bank_reacquisition_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_close_bank_context.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_woodcut_bank_cycle.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_woodcut_bank_scenarios.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_cycle_history.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_brain_progress.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\__init__.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\action_proposal.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\action_lifecycle.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\input_geometry.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\mouse_movement.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\backend_pyautogui.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\backend_pydirectinput.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\executor.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\diagnostics.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_action_proposal.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_action_lifecycle.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_input_geometry.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_mouse_movement.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\execute_next_action.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\bootstrap_window.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\bootstrap_vision.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\run_runelite_bootstrap.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_control_panel.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_config_doctor.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\run_daily_gauntlet.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\run_woodcut_bank_live_qa.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\run_stabilization_suite.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_resource_progress.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_task_policy.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_mission_presets.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_runtime_control.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_mission_snapshot.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_task_policy.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_task_transitions.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_task_state.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_analyzer_contracts.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_inventory_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_target_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_navigation_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_navigation_intent_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_pathing_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_activity_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_intent_overlay_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_brain_context_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_service_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bank_ui_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bank_operation_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_return_to_resource_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_resource_return_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_post_bank_reacquisition_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_close_bank_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_process_inventory_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_intent_stabilizer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_brain_core.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_core_daemon.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_config_doctor.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_control_panel.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_brain_progress.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_service_context.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bank_ui_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bank_operation_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_return_to_resource_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_resource_return_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_post_bank_reacquisition_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_close_bank_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_woodcut_bank_cycle_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_woodcut_bank_scenarios.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_cycle_history.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_action_proposal.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_action_lifecycle.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_input_geometry.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_mouse_movement.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_input_control_executor.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_action_proposal.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_input_geometry.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_mouse_movement.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_pathing_context.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_pathing_matrix.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_run_daily_gauntlet.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_run_woodcut_bank_live_qa.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bootstrap_window.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bootstrap_vision.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_runelite_bootstrap.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_context_service.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_target_processor.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_packet_reader.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_inspect_live_packets.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_telemetry_paths.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_target_coverage.py"],
]


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def tail_lines(text: str, limit: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)


def main() -> int:
    for index, command in enumerate(COMMANDS, start=1):
        print(f"[{index}/{len(COMMANDS)}] {command_text(command)}")
        completed = run_command(command)
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            print("")
            print("FAIL")
            print(f"failed command: {command_text(command)}")
            print("")
            print("last 40 lines:")
            print(tail_lines(output) or "(no output)")
            print("")
            print(f"recommended next command: {command_text(command)}")
            return completed.returncode or 1
    print("")
    print("PASS")
    print("stabilization suite completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
