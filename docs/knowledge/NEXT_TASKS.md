# Next Tasks

| Priority | ID | Task | Success criteria |
| --- | --- | --- | --- |
| 1 | verify_top_floor_bottom_floor_probe | Reconnect RuneLite, stand at the top-floor bank Staircase route state, then run `staircase_floor_selection_probe.py --json` before any full loop rerun. | Current live menu evidence confirms strict object `56231` at `3205,3229,2` exposes `Bottom floor`; if not, the runner fails closed with `floor_selection_option_missing` and does not click `Climb-down`. |
| 2 | record_plane1_staircase_recovery_fixture | Only if already stranded on plane 1 near `3206,3229,1`, rerun the focused plane-1 Staircase recovery probe without loosening route-object matching. | The guide proves a same-plane plane-1 waypoint, strict `Climb-down`, or captured `Bottom floor` option from plane 1; stale daemon context or label-only evidence is rejected and the bot keeps failing closed as `route_guide_no_same_plane_reentry`. |
| 1 | fix_deposit_region_label | Clean up Deposit-All menu-context region classification. | Deposit-All bank UI clicks no longer appear as minimap_click when menu context proves bank UI. |
| 2 | record_second_bank_direct_sample | Record another bank open/deposit/close sample with bankContainerDelta explicit plugin field. | bankContainerDeltaSource is explicit plugin delta or recorded diff, lifecycle PASS. |
| 3 | extract_more_route_templates | Extract templates for new route directions after repeated PASS recordings. | Two clean route recordings compare PASS against the new template. |
| 5 | selected_state_recording | Record a selected item/spell/widget interaction to decide the smallest bridge export. | Schema gap identifies exact selected-state fields needed. |

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
- 2026-06-09: Next live task should start from the current wrong-floor/no-executable context, not from input geometry or banking. Keep the new route-object guards; fix route/context recovery so a stale plane-1 Staircase proposal cannot become a wrong `Climb-up` click.
- 2026-06-09: Wrong-floor context is now explicit. The next productive fix is to record/extract a demonstrated plane-1 route re-entry step if the latest live run still stops at `route_guide_no_same_plane_reentry`.
- 2026-06-09: Existing successful recordings prove a direct plane-2 to plane-0 stair skip, and live trace `20260609_135357_live_woodcutting_loop` proves the top-floor `Bottom-floor` menu row. Next live step should verify that row with `staircase_floor_selection_probe.py --json`; plane-1 recovery remains a separate fixture only if stranded there.
- 2026-06-09: Focused probe `20260609_171349_plane1_staircase_recovery_probe` could not prove recovery because RuneLite had disconnected and plugin menu evidence was stale. Next attempt should first restore loaded scene, then rerun only `plane1_staircase_recovery_probe.py` from the plane-1 state.
- 2026-06-09: Focused top-floor probe `20260609_181650_staircase_floor_selection_probe` found the player still on plane 1 at `3206,3229,1`, so it could not validate the top-floor `Bottom floor` row. Next step is either return to the top-floor state and run `staircase_floor_selection_probe.py --json`, or capture a fresh plane-1 recovery fixture.
<!-- END MANUAL NOTES -->
