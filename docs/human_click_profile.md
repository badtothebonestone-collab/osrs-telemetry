# Human Click Profile

Schema: `human_click_profile.v1`
Status: `PASS`
Generated: `2026-06-07T19:58:17.937Z`
Recordings: `7`
Activity buckets: `banking, camera_input_sample, menu_interaction, route_traversal, woodcutting`

## Recordings Included
- `20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor`: `route_traversal` `PASS`
- `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory`: `woodcutting` `PASS`
- `20260607_120446_Bank_opening_deposit`: `banking` `PASS`
- `20260606_201613_Bank_to_tree_area`: `route_traversal` `PASS`
- `20260607_143719_Open_Bank_Deposit_logs_CLose_bank`: `banking` `PASS`
- `20260607_143917_Bank_stairs_Bottom_floor_option_Woodcutting_area`: `route_traversal` `PASS`
- `20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs`: `woodcutting` `PASS`

## Overall Click Profile
- Raw clicks: `75`
- Target-relative clicks: `33`
- Strong / medium / weak: `31` / `2` / `0`
- Menu row selections: `32`
- Right-click menu opens: `5`
- Duplicate likely clicks: `0`

## Click Landing Quality
- Aim distance median / p75 / p90 px: `194.531` / `394.243` / `465.028`
- Aim buckets: `{'le12': 0, 'le30': 0, 'le80': 3, 'gt80': 19, 'unknown': 0}`
- Clickbox counts: `{'inside': 1, 'outside': 11, 'unknown': 0, 'unavailable': 21}`
- Menu row counts: `{'inside': 12, 'outside': 0, 'unknown': 0, 'missingBounds': 20}`

## Hover / Menu Behavior
- Hover samples: `3`
- Median hover before click ms: `10425.75`

## Camera Behavior
- Camera segments: `20`
- Middle-mouse drags: `4`
- Camera-before-click count: `15`
- Median camera-to-click ms: `9188.0`

## Mouse Path
- Movement segments: `1912`
- Median path length px: `3933.655`
- Median speed px/sec: `88.388`
- Pause count: `582`

## Woodcutting Profile
- Recordings: `2`
- Target-relative clicks: `24`
- Strong/medium rate: `1.0`
- Menu selections: `24`
- Imperfect successful clicks: `24`
- freshChopClickCount: `13`
- inputActionChopClickCount: `13`
- inputTreeTargetEvidenceCount: `0`
- animation879Recordings: `2`
- inventoryFullRecordings: `1`
- normalLogsGainedTotal: `38`

## Banking Profile
- Recordings: `2`
- Target-relative clicks: `0`
- Strong/medium rate: `None`
- Menu selections: `0`
- Imperfect successful clicks: `0`
- bankOpenSeenRecordings: `2`
- bankUiPresentRecordings: `2`
- bankContainerAvailableRecordings: `2`
- bankContainerDeltaAvailableRecordings: `2`
- depositedItems: `[{'id': 1511, 'name': 'Logs', 'quantity': 16, 'before': 16, 'after': 0, 'source': 'inventory_delta|bank_delta|menu_action', 'confirmationLevel': 'bank_container_delta_confirmed'}, {'id': 1511, 'name': 'Logs', 'quantity': 11, 'before': 11, 'after': 0, 'source': 'inventory_delta|bank_delta|menu_action', 'confirmationLevel': 'bank_container_delta_confirmed'}, {'id': 1521, 'name': 'Oak logs', 'quantity': 5, 'before': 5, 'after': 0, 'source': 'inventory_delta|bank_delta|menu_action', 'confirmationLevel': 'bank_container_delta_confirmed'}]`

## Traversal Profile
- Recordings: `4`
- Target-relative clicks: `13`
- Strong/medium rate: `1.0`
- Menu selections: `12`
- Imperfect successful clicks: `12`

## Menu Profile
- Recordings: `5`
- Target-relative clicks: `33`
- Strong/medium rate: `1.0`
- Menu selections: `32`
- Imperfect successful clicks: `32`

## Imperfect But Successful Clicks
- `20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor` event `34`: `None` `Bank table` quality=`strong` distance=`170.141` reason=`matched_expected_postcondition`
- `20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor` event `95`: `Climb-down` `Staircase` quality=`medium` distance=`116.103` reason=`matched_expected_postcondition`
- `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory` event `76`: `Climb-up` `Ladder` quality=`strong` distance=`155.39` reason=`postcondition_positionChanged_inventoryChanged_widgetOpened`
- `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory` event `98`: `Open` `Gate` quality=`strong` distance=`343.839` reason=`matched_expected_postcondition`
- `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory` event `200`: `None` `Stepladder` quality=`strong` distance=`215.128` reason=`postcondition_animationStarted_inventoryChanged_widgetOpened`
- `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory` event `358`: `None` `Stepladder` quality=`strong` distance=`283.805` reason=`postcondition_animationStarted_inventoryChanged_widgetOpened`
- `20260606_201613_Bank_to_tree_area` event `4`: `None` `Bank table` quality=`strong` distance=`108.116` reason=`matched_expected_postcondition`
- `20260606_201613_Bank_to_tree_area` event `41`: `Climb-down` `Staircase` quality=`strong` distance=`138.708` reason=`matched_expected_postcondition`
- `20260606_201613_Bank_to_tree_area` event `100`: `Open` `Door` quality=`strong` distance=`50.537` reason=`matched_expected_postcondition`
- `20260606_201613_Bank_to_tree_area` event `120`: `Climb-up` `Staircase` quality=`strong` distance=`136.125` reason=`postcondition_positionChanged_widgetOpened`

## Missing Data / Caveats
- warning: 20260607_120446_Bank_opening_deposit: target_match_quality.jsonl missing or empty
- warning: 20260607_143719_Open_Bank_Deposit_logs_CLose_bank: target_match_quality.jsonl missing or empty
- missing: target_match_quality
- Tree aim geometry is now recoverable for the short `Chop down / Tree` fixture,
  but selected-Tree clickbox/tile polygon geometry is still missing. Treat that
  sample as aim-distance evidence, not clickbox containment evidence.

## Script-Facing Recommendations
- Prefer strong/medium target-quality evidence over exact clickbox-center replication.
- Allow target-relative variance; outside recovered geometry can still be successful when menu or postcondition evidence proves the action.
- Preserve hover/menu context because it explains many human menu selections and slightly messy clicks.
- Treat camera adjustment as useful pre-action evidence, especially for route transitions and tree visibility.
- Use routeSegments/world/plane for route proof instead of raw click counts.
- Use bank_ui and bank container delta when judging banking; input region labels alone can be misleading.

## Click Planning

`human_click_profile.json` now feeds the advisory `human_click_plan.v1` planner.
The planner compares the live target center/aim point with a deterministic
profile-informed point, but it still requires target/readiness/hover/menu proof
before a plan can be considered click-ready. Missing geometry returns `WARN`
instead of fake coordinates. See `docs/human_click_planning.md`.
