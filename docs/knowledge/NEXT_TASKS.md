# Next Tasks

| Priority | ID | Task | Success criteria |
| --- | --- | --- | --- |
| 1 | record_plane1_staircase_recovery_fixture | Record or extract a short plane-1 Staircase recovery sample from `3206,3229,1` without loosening route-object matching. | The guide proves a same-plane plane-1 waypoint, strict `Climb-down`, or captured `Bottom floor` option from plane 1; otherwise the bot keeps failing closed as `route_guide_no_same_plane_reentry`. |
| 1 | fix_deposit_region_label | Clean up Deposit-All menu-context region classification. | Deposit-All bank UI clicks no longer appear as minimap_click when menu context proves bank UI. |
| 2 | record_second_bank_direct_sample | Record another bank open/deposit/close sample with bankContainerDelta explicit plugin field. | bankContainerDeltaSource is explicit plugin delta or recorded diff, lifecycle PASS. |
| 3 | extract_more_route_templates | Extract templates for new route directions after repeated PASS recordings. | Two clean route recordings compare PASS against the new template. |
| 5 | selected_state_recording | Record a selected item/spell/widget interaction to decide the smallest bridge export. | Schema gap identifies exact selected-state fields needed. |

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
- 2026-06-09: Next live task should start from the current wrong-floor/no-executable context, not from input geometry or banking. Keep the new route-object guards; fix route/context recovery so a stale plane-1 Staircase proposal cannot become a wrong `Climb-up` click.
- 2026-06-09: Wrong-floor context is now explicit. The next productive fix is to record/extract a demonstrated plane-1 route re-entry step if the latest live run still stops at `route_guide_no_same_plane_reentry`.
- 2026-06-09: Existing successful recordings prove a direct plane-2 to plane-0 stair skip but do not capture a `Bottom floor` menu row or any plane-1 recovery. The next task should gather that fixture, not rerun blindly.
<!-- END MANUAL NOTES -->
