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
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\context_service.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_context_query.py"],
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
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_file_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_session_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\candidate_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_readiness_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\action_proposal_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\client_tick_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\safe_aimpoint_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\target_view_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\service_route_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\world_model_core.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\world_model_client.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\external_knowledge_cache.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\external_knowledge.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\external_sources\\osrs_wiki.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\external_sources\\osrs_prices.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\external_sources\\osrsbox.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\knowledge_fabric.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\mcp_server.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\woodcutting_candidate_diagnostics.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_woodcutting_candidates.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\live_readiness.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_live_readiness.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\__init__.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\action_proposal.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\action_lifecycle.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\input_geometry.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\mouse_movement.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\human_input_controller.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\visual_debug_bundle.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\input_integrity.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\arduino_monitor.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\backend_pyautogui.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\backend_pydirectinput.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\backend_arduino_hid.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\executor.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\input_control\\diagnostics.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_action_proposal.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_action_lifecycle.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_input_geometry.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\diagnose_mouse_movement.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\target_geometry_inspector.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\inspect_target_geometry.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\execute_next_action.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\bootstrap_window.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\bootstrap_vision.py"],
    [sys.executable, "-m", "py_compile", "telemetry-viewer\\capture_bootstrap_template.py"],
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
    [sys.executable, "telemetry-viewer\\tests\\test_target_candidate_dedupe.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_navigation_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_navigation_intent_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_pathing_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_activity_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_intent_overlay_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_brain_context_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_service_analyzer.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_service_route_core.py"],
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
    [sys.executable, "telemetry-viewer\\tests\\test_check_live_setup.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_context_query.py"],
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
    [sys.executable, "telemetry-viewer\\tests\\test_human_input_controller.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_arduino_live_input_policy.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_input_integrity.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_visual_debug_bundle.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_world_model_core.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_knowledge_fabric.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_client_tick_core.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_safe_aimpoint_core.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_target_view_core.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_input_control_executor.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_action_proposal.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_woodcutting_candidate_diagnostic.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_readiness.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_core_contracts.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_input_geometry.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_mouse_movement.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_pathing_context.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_pathing_matrix.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_run_daily_gauntlet.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_run_woodcut_bank_live_qa.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bootstrap_window.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_bootstrap_vision.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_capture_bootstrap_template.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_runelite_bootstrap.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_context_service.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_live_target_processor.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_inspect_live_packets.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_maintenance.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_telemetry_paths.py"],
    [sys.executable, "telemetry-viewer\\tests\\test_diagnose_target_coverage.py"],
]


def _command_path(command: list[str]) -> str:
    return str(command[-1]).replace("\\", "/")


def _command_file(command: list[str]) -> str:
    return Path(_command_path(command)).name


def _is_py_compile(command: list[str]) -> bool:
    return len(command) >= 4 and command[1:3] == ["-m", "py_compile"]


def command_group_name(command: list[str]) -> str:
    if _is_py_compile(command):
        path = _command_path(command)
        if "input_control" in path or "execute_next_action.py" in path or "action_" in path:
            return "action/executor compile"
        if "live_" in path or "context_service.py" in path or "run_daily_gauntlet.py" in path:
            return "live stack compile"
        if "diagnose_" in path:
            return "diagnostics compile"
        return "core/analyzer compile"

    name = _command_file(command)
    if name in {"test_telemetry_paths.py"}:
        return "path/session resolution"
    if name in {"test_live_target_processor.py"}:
        return "live file loading/cache"
    if name in {"test_target_analyzer.py", "test_target_candidate_dedupe.py", "test_woodcutting_candidate_diagnostic.py", "test_live_core_contracts.py"}:
        return "candidate classification/scoring"
    if name in {"test_task_policy.py", "test_task_state.py", "test_task_transitions.py", "test_resource_progress.py"}:
        return "woodcutting profile tests"
    if name in {"test_live_readiness.py"}:
        return "readiness gate"
    if name in {"test_action_proposal.py", "test_diagnose_action_proposal.py"}:
        return "action proposal"
    if name in {"test_action_lifecycle.py", "test_input_control_executor.py", "test_input_geometry.py", "test_mouse_movement.py", "test_human_input_controller.py", "test_client_tick_core.py", "test_safe_aimpoint_core.py"}:
        return "executor/action lifecycle"
    if name in {"test_context_service.py", "test_live_context_query.py", "test_live_control_panel.py", "test_mission_snapshot.py"}:
        return "context service/query"
    if name.startswith("test_diagnose_") or name.endswith("_diagnostic.py") or name in {
        "test_run_daily_gauntlet.py",
        "test_run_woodcut_bank_live_qa.py",
        "test_inspect_live_packets.py",
        "test_check_live_setup.py",
    }:
        return "diagnostics smoke"
    if "bank" in name or "service" in name or "return" in name or "pathing" in name or "navigation" in name:
        return "cycle analyzers"
    if "bootstrap" in name:
        return "bootstrap/input startup"
    return "core/analyzer behavior"


def build_command_groups(commands: list[list[str]]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    by_name: dict[str, dict[str, object]] = {}
    for command in commands:
        name = command_group_name(command)
        group = by_name.get(name)
        if group is None:
            group = {"name": name, "commands": []}
            by_name[name] = group
            groups.append(group)
        group_commands = group["commands"]
        assert isinstance(group_commands, list)
        group_commands.append(command)
    return groups


COMMAND_GROUPS = build_command_groups(COMMANDS)


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def tail_lines(text: str, limit: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)


def main() -> int:
    index = 0
    total = len(COMMANDS)
    for group in COMMAND_GROUPS:
        print("")
        print(f"== {group['name']} ==")
        commands = group["commands"]
        assert isinstance(commands, list)
        for command in commands:
            index += 1
            print(f"[{index}/{total}] {command_text(command)}")
            completed = run_command(command)
            output = (completed.stdout or "") + (completed.stderr or "")
            if completed.returncode != 0:
                print("")
                print("FAIL")
                print(f"group: {group['name']}")
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
