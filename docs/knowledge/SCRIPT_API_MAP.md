# Script API Map

| Function/object | Purpose | Consumes | Status |
| --- | --- | --- | --- |
| get_bank_state(source) | Bank/deposit UI state, bank container availability, bank_ui freshness. | banking_lifecycle.bank / context bank_state | implemented |
| get_banking_lifecycle(source) | Compact lifecycle status, phase, confidence, warnings. | banking_lifecycle.json / context banking_lifecycle | implemented |
| is_bank_open(source) | Boolean direct bank-open proof. | bank_state.bankOpen | implemented |
| is_deposit_box_open(source) | Boolean direct deposit-box proof. | bank_state.depositBoxOpen | implemented |
| get_active_bank_like_interface(source) | bank, deposit_box, or unknown. | bank_state.activeBankLikeInterface | implemented |
| get_inventory_delta(source) | Free slots and deposited/withdrawn item deltas. | banking_lifecycle.inventory | implemented |
| get_deposit_result(source) | Deposit complete, items, confidence, confirmation level. | banking_lifecycle.deposit | implemented |
| get_deposited_items(source) | List of deposited item summaries. | deposit_result.depositedItems | implemented |
| did_deposit_item(source, item_id) | True when a deposited item id is present. | deposit_result.depositedItems | implemented |
| get_banking_missing_capabilities(source) | Compact list of missing banking capabilities. | banking_lifecycle.missingCapabilities | implemented |
| get_combat_state(source) | Compact combat targeting, hitsplat, hostile NPC, and health evidence. | interruption_lifecycle.combat / combat_state | implemented |
| is_in_combat(source) | Boolean direct combat observation. | interruption_lifecycle.combat.combatObserved | implemented |
| get_interruption_lifecycle(source) | Compact interruption status, cause, resume, confidence, and warnings. | interruption_lifecycle.json | implemented |
| was_task_interrupted(source) | Boolean task interruption/resume or combat/message/stat signal. | interruption_lifecycle.interruptionDetected | implemented |
| get_interruption_cause(source) | Primary interruption cause such as hostile_npc, mugger_attack, level_up, or unknown. | interruption_lifecycle.primaryCause | implemented |
| get_combat_damage_summary(source) | Compact damage taken/dealt, opponent, HP, hitsplat, actor death, and task resume evidence. | combat_damage_summary.json | implemented |
| get_damage_taken(source) | Damage taken total, hitsplat count, and HP before/after evidence. | combat_damage_summary.damageTaken / health | implemented |
| get_damage_dealt(source) | Damage dealt total, hitsplat count, and target evidence. | combat_damage_summary.damageDealt | implemented |
| get_primary_opponent(source) | Primary opponent name/id/confidence. | combat_damage_summary.primaryOpponent | implemented |
| did_take_damage(source) | Boolean damage-taken proof from amount, HP change, or player hitsplats. | combat_damage_summary.damageTaken | implemented |
| did_deal_damage(source) | Boolean damage-dealt proof from amount or opponent hitsplats. | combat_damage_summary.damageDealt | implemented |
| get_recent_hitsplats(source) | Recent hitsplat evidence from combat_state/interruption lifecycle. | combat_state.recentHitsplats | implemented |
| get_recent_stat_changes(source) | Recent stat/level change evidence. | combat_state.recentStatChanges | implemented |
| get_recent_game_messages(source) | Recent chat/game message evidence. | combat_state.recentChatMessages | implemented |
| get_human_click_profile(source) | Compact aggregate human click/camera profile. | human_click_profile.json | implemented |
| get_task_click_profile(activity, source) | Task-specific click profile bucket. | human_click_profile.taskProfiles | implemented |
| get_click_landing_profile(activity, source) | Aim distance and clickbox/menu-row landing summary. | human_click_profile.landing | implemented |
| get_camera_action_profile(activity, source) | Camera segment and camera-before-click summary. | human_click_profile.camera | implemented |
| get_click_planning_context(activity, source) | Compact task, route, banking, target/readiness, and profile context for advisory planning. | task_script_api runtime state + human_click_profile | implemented |
| get_human_click_plan(target, action, activity, source) | Human-profile-informed advisory click plan with blockers, confidence, and center-vs-profile aim. | input_control.click_planner | implemented |
| get_next_click_plan(source) | Best available advisory next-click plan from current script evidence. | task_script_api + click_planner | implemented |
| get_woodcutting_loop_lifecycle(source) | Compact full woodcutting task loop phase and next expected phase. | woodcutting_loop_lifecycle.json | implemented |
| get_current_task_phase(source) | Current woodcutting loop phase. | woodcutting_loop_lifecycle.currentPhase | implemented |
| get_next_expected_phase(source) | Next expected woodcutting loop phase. | woodcutting_loop_lifecycle.nextExpectedPhase | implemented |
| is_inventory_full_for_woodcutting(source) | Boolean inventory-full gate for routing to bank. | woodcutting_loop_lifecycle.woodcutting | implemented |
| did_deposit_logs(source) | Boolean proof that logs were deposited. | woodcutting_loop_lifecycle.banking.depositedItems | implemented |
| should_route_to_bank(source) | True when the loop next phase is route_to_bank. | woodcutting_loop_lifecycle.nextExpectedPhase | implemented |
| should_route_to_trees(source) | True when the loop next phase is route_to_woodcutting_area. | woodcutting_loop_lifecycle.nextExpectedPhase | implemented |
| was_interrupted(source) | Boolean interruption flag from loop/interruption lifecycle. | woodcutting_loop_lifecycle.interruptions | implemented |
| did_resume_after_interruption(source) | Boolean task-resumed proof from loop/interruption lifecycle. | woodcutting_loop_lifecycle.interruptions | implemented |
| build_task_script_evidence_plan(script) | Variables a script must prove before/after primitives. | task_script_api runtime evidence catalog | implemented |
| compare_task_runtime_evidence_snapshots(before, after) | State-delta proof for script steps. | task runtime evidence snapshots | implemented |

Example:

```python
import task_script_api as api
result = api.get_deposit_result(recording_folder)
api.did_deposit_item(recording_folder, 1511)
```

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
<!-- END MANUAL NOTES -->
