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
| live_route_wrong_floor_recovery | high | route/context/action_proposal | open | From `3206,3229,1`, rebuild route/resource context so the bot can get back to a valid woodcutting loop state without loosening route-object matching. |

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
- 2026-06-09: `return_route_staircase_hover_menu` was guarded in `client_tick_core.py` and `input_control/executor.py`. Generic `Climb` no longer matches `Climb-up`/`Climb-down` by substring, expected object ids must match when present, and wrong-plane route targets are blocked before click. Latest live state is now wrong-floor/no-executable-context at `3206,3229,1`.
<!-- END MANUAL NOTES -->
