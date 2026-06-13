# Next Tasks

| Priority | ID | Task | Success criteria |
| --- | --- | --- | --- |
| 1 | validate_authenticated_live_start | Validate the discovered Jagex Launcher RuneLite quick-launch path and loaded-scene recovery. | start_game_command.py --validate-live reports jagex_launcher_runelite_quick_launch.; context_service.py --ensure-loaded-scene reaches loadedSceneVerified=true without dev_launch_not_loaded. |
| 1 | fix_deposit_region_label | Clean up Deposit-All menu-context region classification. | Deposit-All bank UI clicks no longer appear as minimap_click when menu context proves bank UI. |
| 1 | validate_collect_until_full_after_tree_click | Continue validation after confirmed post-return Tree clicks until inventory full and banking is reached again. | Live run keeps `Chop down / Tree` executing, reaches inventory full or near full, then proposes route_to_bank; if combat interruption prevents progress it reports `interruption_active_no_recovery_policy` or a specific resume blocker. |
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
- 2026-06-09: Fresh probe `20260609_185122_plane1_staircase_recovery_probe` captured strict, fresh `Climb-down / Staircase` evidence for object `16672` at `3204,3229,1`; `Bottom floor` was not present on plane 1. Next live run should validate the `1 -> 0` postcondition for that recovery, not retry stale evidence collection.
- 2026-06-13: Before the next live-loop run, fix startup/authentication: the recovery ladder now attempts Click here, visible Play Now, and Start Game relaunch, but the configured Start Game command is a `dev_gradle_run` and ended at `dev_launch_not_loaded`.
- 2026-06-13: Authenticated live start discovery is now implemented. Next live gate should run Jagex quick-launch recovery via `context_service.py --ensure-loaded-scene`, then geometry/calibration, before trying the real loop.
- 2026-06-13: Latest Jagex quick-launch recovery reached RuneLite but stayed on disconnected/login with `stale_login_screen_after_relaunch`. Next task is to clear that login/disconnect surface through existing safe recovery or launcher session state, then rerun loaded-scene recovery.
- 2026-06-13: Visible `disconnected_ok` is now clicked through Arduino before any manual-login result, including after Start Game relaunch. Latest recovery blocker is `visible_button_no_transition`: safe OK clicks were acknowledged but the client stayed on disconnected/login. Next task is to make that surface actually transition or identify why RuneLite/Jagex session stays disconnected.
- 2026-06-13: Strict lower-menu route selection now opens the route menu for plane-1 `Climb-down / Staircase`, but live `MenuOpened` samples report zero `menuBounds`. Next task is row geometry export or an already-tested safe row-selection fallback, not more route-guide or recovery work.
- 2026-06-13: Strict lower-menu row selection is now validated live: `bot_runs\20260613_145500_live_woodcutting_loop` clicked `Climb-down / Staircase` for object `16672` and verified plane `1 -> 0`. Next task is the later resource-area blocker `resource_target_hover_safety_skip`, not more Staircase route work.
- 2026-06-13: Resource hover safety is fixed and live-validated: `bot_runs\20260613_152350_live_woodcutting_loop` sent six `Chop down / Tree` actions, gained logs, and observed animation `879`. Next task is longer collect-until-full/bank validation, with interruption/resume handling if Giant spider combat remains active.
<!-- END MANUAL NOTES -->
