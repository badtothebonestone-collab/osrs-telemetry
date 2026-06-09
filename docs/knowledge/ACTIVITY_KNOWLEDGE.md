# Activity Knowledge

## Banking

- Known signals: bank_ui, bankOpen/depositBoxOpen, bank widget/root, bank container, inventory delta, deposit/withdraw menu context
- Useful fields: bankContainerDeltaAvailable, depositConfirmationLevel, depositedItems, missingCapabilities
- Lifecycle outputs: banking_lifecycle.json, summary banking fields
- Proven recordings: 20260607_143719_Open_Bank_Deposit_logs_CLose_bank
- Gaps: deposit_all_region_classification, bank_container_slot_provenance

## Route / Traversal

- Known signals: routeSegments, world path, plane changes, target quality, menu row evidence
- Useful fields: routeName, routeSegments, routeTemplateStatus, routeState
- Lifecycle outputs: traversal_lifecycle.json, route_template_comparison.json, route_history_summary.json
- Proven recordings: 20260602_234307_manual_action-woodcuting_area_to_bank, 20260606_094608_manual_route-bank_to_woodcutting_area_v2, 20260606_105427_manual_route-bank_to_woodcutting_area_v3, 20260606_121630_bank_to_WC, 20260606_154108_manual_route-bank_to_woodcutting_area, 20260606_161802_manual_route-bank_to_woodcutting_area, 20260606_165229_manual_action-menu_row_validation_live_mirror_controlled, 20260606_171522_manual_route-bank_to_woodcutting_area, 20260606_180200_manual_recording_20260606_180153, 20260606_181952_manual_recording_20260606_181945, 20260606_192931_Tree_area_to_Bank, 20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor, 20260607_143415_Wood_Cutting_area_to_Bank, 20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area
- Gaps: route_template_coverage

## Woodcutting

- Known signals: tree target, Chop down, animation, inventory log delta, target depletion, interruption stop/resume gaps, combat damage/resume evidence
- Useful fields: phase, normalLogsGained, freshChopClickCount, interruption.interruptionType, combatDamageSummary.damageTakenTotal
- Lifecycle outputs: woodcutting_lifecycle.json, interruption_lifecycle.json, combat_damage_summary.json
- Proven recordings: 20260602_223444_manual_action-Tree_cutting, 20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs
- Gaps: pure_normal_logs_woodcutting_fixture, combat_damage_source_attribution_multi_actor

## Woodcutting Loop

- Known signals: woodcutting lifecycle, inventory fullness, route to bank, bank deposit, route to trees, interruption resume
- Useful fields: loopState, currentPhase, nextExpectedPhase, detectedPhases, depositComplete, taskResumed
- Lifecycle outputs: woodcutting_loop_lifecycle.json
- Proven recordings: 20260606_201613_Bank_to_tree_area, 20260607_104613_Woodcutting_area_to_bank, 20260607_120446_Bank_opening_deposit, 20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory, 20260607_154606_Wood_cutting_attacked, 20260607_171427_Wood_cutting_attacked, 20260608_135929_live_woodcutting_loop_20260608_135928
- Gaps: pure_normal_logs_woodcutting_fixture

## Combat / Interruption

- Known signals: combat_state, NPC/player interaction, hitsplats, HP changes, chat/game messages, stat changes, task stop/resume
- Useful fields: interruptionType, primaryCause, taskResumed, combat.hitsplatsSeen, damageTakenTotal, damageDealtTotal, primaryOpponent
- Lifecycle outputs: interruption_lifecycle.json, combat_damage_summary.json
- Proven recordings: 20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs
- Gaps: combat_damage_source_attribution_multi_actor

## Menu Interaction

- Known signals: menuBounds, entries, row bounds, MenuOptionClicked, target match
- Useful fields: menuSelectionCount, rowGeometryProven, selectedSnapshotId
- Lifecycle outputs: menu_interaction_summary.json, target_match_summary.json
- Proven recordings: none yet
- Gaps: selected_item_spell_widget

## Input / Camera / Arduino

- Known signals: input_events, raw OS clicks, camera segments, mapping, mirror verification, human click/camera profile
- Useful fields: inputPathIntegrity, coordinateTransform, clickPolicyUsed, duplicateClickLikelyCount, medianAimDistancePx, imperfectSuccessfulClickCount
- Lifecycle outputs: input_action_summary.json, camera_behavior_summary.json, input_path_integrity_summary.json, human_click_profile.json
- Proven recordings: none yet
- Gaps: live_mirror_ownership, clickbox_geometry_incomplete_for_profile, menu_row_geometry_profile_gaps

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
<!-- END MANUAL NOTES -->
