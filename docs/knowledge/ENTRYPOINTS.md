# Entrypoints And Ownership

Future cleanup and live-bot work must reuse these canonical paths.

| Responsibility | Canonical module/function/command | Do not duplicate in | Notes |
| --- | --- | --- | --- |
| Start Game | `telemetry-viewer\start_game_command.py` (`resolve_start_game_command`, `launch_start_game`) | `telemetry_ui.py`, `bot_eval_runner.py`, recovery scripts | UI, recovery, and bot eval share launch classification. `devStartCommand` is for Gradle/plugin testing; live recovery must use `liveStartCommand`, discovered Jagex quick launch, or an already-loaded client. |
| Loaded-scene recovery | `liveness_recovery_core.py`; `context_service.py --ensure-loaded-scene` | Bot runners, UI, executor ad hoc relaunch code | `execute_next_action.py --auto-recover-loaded-scene` and `bot_eval_runner.py --auto-recover-loaded-scene` may call this path. |
| Live readiness | `live_readiness_core.py`; bot preflight/readiness wrappers | Bot eval one-off checks, UI-only checks | Use shared readiness logic or context-service equivalent. |
| Input geometry | `input_control\input_geometry.py` (`resolve_input_geometry_status`, `repair_runelite_focus`, `validate_screen_point_inside_geometry`) | `live_readiness_core.py`, `bot_eval_runner.py`, `input_control\executor.py`, `telemetry_ui.py` ad hoc geometry checks | `bot_eval_runner.py --check-input-geometry` and executor pre-click gates must use this resolver. |
| Record Everything | `telemetry_ui.py` Simple Mode; `manual_recorder.py`; `analyze_manual_recording.py`; `update_project_knowledge.py` | Per-task recorder forks | Broad capture is the default and analyzer decides what matters. |
| Knowledge base | `docs\knowledge`; `telemetry-viewer\knowledge_base`; `update_project_knowledge.py` | Chat-only memory, stale handoff docs | Update docs and JSON indexes after telemetry/API/analyzer/context changes. |
| Bot eval | `bot_eval_runner.py` | New live-loop launchers | Replay, preflight, live smoke, and guarded live action belong here. |
| Script-facing API | `task_script_api.py`; `knowledge_fabric.py` | Scripts parsing raw recording JSON | Scripts consume compact helpers and evidence variables. |
| Context API | `context_service.py`; `mcp_server.py` | New mutable MCP/input endpoints | Context/MCP surfaces are read-only unless explicitly changed. |
| Click/action planning | `input_control\click_planner.py`; `input_control\action_proposal.py`; `input_control\executor.py`; `candidate_core.py` | Bot eval runner, route monitor, UI | Planner is advisory until guarded executor/readiness proves live action is safe. |
| Routes | `route_template.py`; `route_monitor.py`; `route_demonstration.py`; `traversal_lifecycle.py` | Bot eval route parsers, raw-click template logic | Use route segments/templates/guides; raw clicks are support evidence. |
| Banking | `TelemetryPlugin.java` (`bank_ui`, `bankContainerDelta`); `banking_lifecycle.py`; task_script_api banking helpers | Inventory-only deposit inference paths | Direct bank UI/container evidence outranks inference. |
| Woodcutting | `woodcutting_lifecycle.py`; `woodcutting_loop_lifecycle.py` | Bot eval phase reimplementation | Loop state combines task lifecycle evidence. |
| Combat/interruption | `interruption_lifecycle.py`; `combat_damage_summary.py` | Woodcutting-only combat heuristics | Combat cause/damage summaries stay independent. |
| Human profile | `human_click_profile.py`; `click_planner.py` consumes it in advisory mode | Executor randomization or click shortcuts | Human profile informs tolerances and recommendations, not bypasses. |

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
- 2026-06-13: Login/startup surfaces belong to `liveness_recovery_core.py` plus `run_runelite_bootstrap.py` and `start_game_command.py`. Live bot code should not report `manual_login_required` before the canonical ladder has tried safe Click here/Play Now/disconnected/Start Game recovery and written recovery artifacts.
- 2026-06-13: Visible safe recovery buttons must be handled by the canonical loaded-scene recovery path before manual login is reported. Post-relaunch visible `disconnected_ok`, `play_now`, or `click_here_to_play` states must re-enter `run_runelite_bootstrap.py`; repeated no-transition clicks stop as `visible_button_no_transition`.
<!-- END MANUAL NOTES -->
