# Bot Script Data Impact Audit

Date: 2026-06-07

Scope: Record Everything capabilities that have been promoted from recording/analyzer outputs into script-readable APIs, Knowledge Fabric runtime evidence, and the bot/task decision layer.

## Impact Table

| Capability | Recording proof | Analyzer output | Context API | MCP | task_script_api | knowledge_fabric | Used by execute_next_action / bot logic? | Tests | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| banking_lifecycle | PASS direct bank sample `20260607_120446_Bank_opening_deposit` | `banking_lifecycle.json`, summary banking fields | `banking`, `banking_lifecycle`, `bank_state`, `deposit_result` | Banking tools and `get_context` | `get_banking_lifecycle`, `get_bank_state`, `get_deposit_result`, `did_deposit_item` | `bankingLifecycle`, `bankState`, `depositResult` runtime variables | Yes, deposit readiness/proof and deposit-complete next-phase inference | `test_task_script_api.py`, `test_context_service.py` | used_by_bot |
| bank_ui | PASS direct bank sample | Consumed by banking lifecycle | `bank_state`, compact banking response | Banking tools and `get_context` | `get_bank_state`, `is_bank_open`, `is_deposit_box_open` | `bankState`, `bankingLifecycle` | Yes, bank-open and deposit-box proof drive banking phase confidence | `test_task_script_api.py`, `test_context_service.py` | used_by_bot |
| bankContainerDelta | PASS direct bank sample | `bankContainerDeltaAvailable`, confirmation level | `deposit_result`, compact banking response | Banking/deposit tools | `get_deposit_result`, `did_deposit_item` | `depositResult` | Yes for deposit proof; not used for path/action choice beyond deposit completion | `test_task_script_api.py` | used_by_bot |
| deposit_result | PASS direct bank sample | Summary deposit fields | `deposit_result` | Deposit tool / `get_context` | `get_deposit_result`, `get_deposited_items`, `did_deposit_item` | `depositResult` | Yes, depositComplete now infers route back to trees | `test_task_script_api.py` | used_by_bot |
| woodcutting_lifecycle | Woodcutting PASS fixtures, interrupted woodcutting PASS | `woodcutting_lifecycle.json` | `woodcutting_lifecycle` | Woodcutting lifecycle tool / `get_context` | `get_woodcutting_lifecycle` | Input to `woodcuttingLoopLifecycle`; live resource variables remain primary | Indirect: loop lifecycle and resource/inventory state influence next primitive | `test_task_script_api.py`, `test_woodcutting_lifecycle.py` | script_api_only |
| woodcutting_loop_lifecycle | Full loop and partial loop fixtures | `woodcutting_loop_lifecycle.json` | `woodcutting_loop`, `next_expected_phase` | `get_context` with loop needs | `get_woodcutting_loop_lifecycle`, `get_next_expected_phase`, `should_route_to_bank`, `should_route_to_trees` | `woodcuttingLoopLifecycle` runtime variable | Yes, next expected phase now influences advisory primitive selection | `test_task_script_api.py`, `test_knowledge_fabric.py` | used_by_bot |
| route_monitor | Route monitor PASS fixtures | `route_monitor_status.json`, route history summaries | `route_monitor`, `route_history` | `get_context` with route needs | `get_route_monitor_status`, `get_route_state`, `get_current_route_segment`, `get_next_route_segment`, `is_off_route` | `routeMonitor` runtime variable | Yes, offRoute now forces wait-for-evidence in run readiness | `test_task_script_api.py`, `test_knowledge_fabric.py` | used_by_bot |
| route_history | Persistent route session fixtures | `route_history_summary.json` | `route_history`, `route_session_state` | `get_context` with route needs | Fallback source for `get_route_monitor_status` | Route monitor/session state can be surfaced | Indirect through route monitor compact status | `test_route_monitor.py`, `test_task_script_api.py` | script_api_only |
| route_template_comparison | Bank/tree route PASS comparisons | `route_template_comparison.json` | Route comparison summaries | `get_context` route needs | Not directly exposed; consumed by route monitor/loop reports | Not direct runtime variable | Indirect only; scripts should use route monitor/loop state | `test_route_template.py`, `test_route_monitor.py` | analyzer_only |
| interruption_lifecycle | Mugger/interrupted woodcutting PASS | `interruption_lifecycle.json` | `interruption_lifecycle`, combat/interruption response | Interruption/combat tools | `get_interruption_lifecycle`, `was_task_interrupted`, `did_task_resume`, `get_interruption_cause` | `interruptionLifecycle` runtime variable | Yes, interrupted and not resumed now forces wait-for-evidence | `test_task_script_api.py`, `test_interruption_lifecycle.py` | used_by_bot |
| combat_damage_summary | Mugger fixture PASS | `combat_damage_summary.json` | `combat_damage_summary`, damage fields | Combat damage tools | `get_combat_damage_summary`, `get_damage_taken`, `get_damage_dealt`, `did_take_damage` | `combatDamageSummary` runtime variable | Not yet action-changing; script-readable for safety/review | `test_task_script_api.py`, `test_combat_damage_summary.py` | script_api_only |
| human_click_profile | Aggregated route/woodcutting/banking recordings | `human_click_profile.json` | `human_click_profile`, click/camera profile needs | `get_context` profile needs | `get_human_click_profile`, task/click/camera profile helpers | `humanClickProfile` runtime variable and `human_click_profile()` query | Executor receives compact advisory handoff; live click generation unchanged | `test_task_script_api.py`, `test_knowledge_fabric.py`, `test_input_control_executor.py` | script_api_only |
| target_match_quality | Route/menu/woodcutting target-quality reports | `target_match_quality.jsonl`, summary | Target/menu summaries | `get_context` and debug queries | Not direct; reflected in human click profile and analyzer reports | Not direct runtime variable | Analysis and guardrail evidence only in this pass | `test_target_match_quality.py` | analyzer_only |
| menu_interactions | Menu row validation and route examples | `menu_interactions.jsonl`, summary | Menu interaction/debug summaries | `get_action_input_visibility` for live menu state | Runtime `hoverTarget`, `menuOptionClicked`; recording summary not direct | Live menu evidence variables | Live menu state is bot-used; offline menu interaction summaries are analysis-only | `test_input_control_executor.py`, `test_menu_interaction_model.py` | used_by_bot |
| coordinate_alignment | Menu/route validation reports | `coordinate_alignment_summary.json` | Coordinate/debug summaries | Debug/context queries | Not direct | Action input visibility/debug evidence | Used as diagnostics and input path guardrail, not task phase logic | `test_context_service.py`, coordinate tests | context_only |
| input_path_integrity | Input integrity summaries and live input checks | `input_path_integrity_summary.json` | Input integrity/debug summaries | `get_action_input_visibility` | Runtime `inputIntegrity` variable | Runtime evidence integrity | Yes as a safety/readiness guardrail, not a task objective | `test_task_script_api.py`, `test_input_control_executor.py` | used_by_bot |
| bot_eval_runner | Replay proof from `20260607_171427_Wood_cutting_attacked` | Consumes lifecycle/profile outputs and writes `bot_eval_summary.json` | Not a normal context field | Not exposed separately | Calls readiness, loop, deposit, route, interruption, combat, and click-plan helpers | Uses the same script-facing runtime evidence shape | Yes for evaluation: proves phase decisions before live changes | `test_bot_eval_runner.py` | used_by_bot |

## What Already Impacts Bot Decisions

- Banking and deposit data: bank-open proof, deposit result, and deposited item evidence are script-readable and used by readiness/runtime evidence checks.
- Woodcutting loop phase: `nextExpectedPhase` now influences the advisory next primitive, such as routing to bank, banking deposit, returning to trees, or continuing cutting.
- Route monitor: off-route state now takes priority over loop phase and asks the bot to wait for evidence instead of continuing blindly.
- Interruption lifecycle: an interruption without resume proof now asks the bot to wait/recover instead of proceeding as if the task is normal.
- Input integrity and live menu evidence: still used as guardrails around action execution.

## What Is Still Mostly Analysis-Only

- `route_template_comparison` should remain analyzer/template proof. Scripts should consume route monitor or loop state instead.
- `target_match_quality`, `coordinate_alignment`, and offline `menu_interactions` are mostly diagnostic inputs. Live menu/hover proof is script-relevant; offline summaries are for review and profile building.
- `combat_damage_summary` is now script-readable, but this pass does not change behavior based on damage totals.
- `human_click_profile` is handed to the executor as advisory click/camera guidance; it does not alter live click placement yet.

## Newly Wired In This Pass

- Added script APIs for woodcutting lifecycle and route monitor state.
- Added `routeMonitor` and `humanClickProfile` to Knowledge Fabric task runtime evidence.
- Added advisory next-primitive inference from woodcutting loop, route off-route, deposit completion, and interruption resume state.
- Added `human_click_profile_executor_handoff.v1` so future input code can consume click landing/camera profile data without parsing profile JSON directly.

## Recommended Next Integration

Run `bot_eval_runner.py` in a bounded live mode once the daemon and game are ready. The replay pass already produced zero decision mismatches; the next useful proof is the same trace shape with real guarded action/postcondition evidence.

## 2026-06-07 Replay Bot Eval Addendum

Command:

```powershell
python telemetry-viewer\bot_eval_runner.py --task woodcutting_loop --recording "C:\Users\badto\osrs-telemetry\recordings\20260607_171427_Wood_cutting_attacked" --max-actions 100 --record-everything --analyze-after --json
```

Output:

`C:\Users\badto\osrs-telemetry\bot_runs\20260607_204642_woodcutting_loop_eval`

Result:

- Mode: replay, because the live daemon status probe timed out.
- Decisions: 8.
- Actions: 8 replay action records, no live input commands sent.
- Postconditions: 8 PASS.
- Decision mismatches: 0.
- Click plans: WARN/advisory only because replay did not provide fresh live target geometry/readiness.

The replay confirms the script layer chooses:

- `collect` for cutting and resumed cutting.
- `bank` when inventory is full or routing to bank.
- `deposit` while bank UI/deposit evidence is active.
- `return_to_resource` after deposit completion and while routing back to trees.
- `collect` after an interruption with task-resumed evidence.
