# Open Gaps

| Gap | Severity | Layer | Status | Suggested next task |
| --- | --- | --- | --- | --- |
| deposit_all_region_classification | low | analyzer | open | Use menu context to override screen-region labels for bank UI menu clicks. |
| bank_container_slot_provenance | medium | plugin/analyzer | open | Add ItemContainerChanged provenance only if a task needs slot-level proof. |
| selected_item_spell_widget | medium | plugin | open | Record an item-on-object or spell interaction sample, then export only the missing selected state. |
| route_template_coverage | medium | recording/analyzer | open | Extract templates for new proven route directions after two clean recordings. |
| live_mirror_ownership | medium | input/arduino | open | Keep normal Record Everything map-only/no-live-click; test live mirror only in isolated validation recordings. |
| input_geometry_live_source_stale | medium | telemetry/live_readiness/input | open | Restore or attach RuneLite plus context/snapshot endpoints, then rerun --check-input-geometry and allow live actions only after input_geometry_pass. |
| clickbox_geometry_incomplete_for_profile | medium | analyzer/geometry | open | Record/verify a woodcutting sample with object clickbox or tile polygon export so click-plan validation can compare actual clicks against hull containment, not only aim distance. |
| menu_row_geometry_profile_gaps | low | analyzer | open | Keep menu hover/target/postcondition evidence as backup when row bounds are absent. |
| pure_normal_logs_woodcutting_fixture | low | recording | open | Record one tree-area sample that fills inventory with only normal Logs if a task needs item-specific timing. |
| combat_damage_source_attribution_multi_actor | low | recording | open | If combat routing matters later, collect a multi-NPC interruption fixture to validate source attribution under ambiguity. |
| validate_human_click_plans_against_recordings | medium | recording/input_control | open | Compare dry-run planned aim points against successful human clicks in future Record Everything fixtures before changing live click generation. |
| knowledge_manual_curation | low | docs | open | After each milestone, add one concise manual note when generated summaries miss intent. |
| live_route_wrong_floor_recovery | high | route/context/action_proposal | open | Route re-entry now fails closed with `route_guide_no_same_plane_reentry`; add or record a demonstrated plane-1 recovery step before allowing a live route click from `3206,3229,1`. |

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
- 2026-06-09: `return_route_staircase_hover_menu` was guarded in `client_tick_core.py` and `input_control/executor.py`. Generic `Climb` no longer matches `Climb-up`/`Climb-down` by substring, expected object ids must match when present, and wrong-plane route targets are blocked before click. Latest live state is now wrong-floor/no-executable-context at `3206,3229,1`.
- 2026-06-09: Wrong-floor re-entry now uses `route_demonstration.resolve_reentry`, `task_script_api.get_route_guide_reentry`, and `KnowledgeFabric.route_guide_reentry`. The current `Bank_to_Woodcutting_area` guide has no same-plane point or interaction for `3206,3229,1`, so the safe blocker is `route_guide_no_same_plane_reentry`, not generic `no_executable_action`.
- 2026-06-09: Latest real live rerun `20260609_154147_live_woodcutting_loop` passed geometry and loaded-scene recovery, sent zero gameplay actions, and stopped with `route_guide_no_same_plane_reentry`. Next step is a demonstrated plane-1 re-entry recording/extraction, not guard loosening.
<!-- END MANUAL NOTES -->
