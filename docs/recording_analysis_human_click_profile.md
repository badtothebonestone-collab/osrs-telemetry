# Human Click Profile

Schema: `human_click_profile.v1`
Status: `PASS`
Generated: `2026-06-07T19:14:11.157Z`
Recordings: `4`
Activity buckets: `banking, camera_input_sample, menu_interaction, route_traversal, woodcutting`

## Recordings Included
- `20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor`: `route_traversal` `PASS`
- `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory`: `woodcutting` `PASS`
- `20260607_120446_Bank_opening_deposit`: `banking` `PASS`
- `20260606_201613_Bank_to_tree_area`: `route_traversal` `PASS`

## Overall Click Profile
- Raw clicks: `23`
- Target-relative clicks: `11`
- Strong / medium / weak: `10` / `1` / `0`
- Menu row selections: `10`
- Right-click menu opens: `3`
- Duplicate likely clicks: `0`

## Click Landing Quality
- Aim distance median / p75 / p90 px: `138.708` / `192.635` / `283.805`
- Aim buckets: `{'le12': 0, 'le30': 0, 'le80': 2, 'gt80': 9, 'unknown': 0}`
- Clickbox counts: `{'inside': 1, 'outside': 8, 'unknown': 0, 'unavailable': 2}`
- Menu row counts: `{'inside': 5, 'outside': 0, 'unknown': 0, 'missingBounds': 5}`

## Hover / Menu Behavior
- Hover samples: `3`
- Median hover before click ms: `10425.75`

## Camera Behavior
- Camera segments: `9`
- Middle-mouse drags: `1`
- Camera-before-click count: `6`
- Median camera-to-click ms: `8523.5`

## Mouse Path
- Movement segments: `662`
- Median path length px: `4038.784`
- Median speed px/sec: `82.049`
- Pause count: `261`

## Woodcutting Profile
- Recordings: `1`
- Target-relative clicks: `4`
- Strong/medium rate: `1.0`
- Menu selections: `4`
- Imperfect successful clicks: `4`
- freshChopClickCount: `2`
- inputActionChopClickCount: `2`
- inputTreeTargetEvidenceCount: `0`
- animation879Recordings: `1`
- inventoryFullRecordings: `1`
- normalLogsGainedTotal: `11`

## Banking Profile
- Recordings: `1`
- Target-relative clicks: `0`
- Strong/medium rate: `None`
- Menu selections: `0`
- Imperfect successful clicks: `0`
- bankOpenSeenRecordings: `1`
- bankUiPresentRecordings: `1`
- bankContainerAvailableRecordings: `1`
- bankContainerDeltaAvailableRecordings: `1`
- depositedItems: `[{'id': 1511, 'name': 'Logs', 'quantity': 16, 'before': 16, 'after': 0, 'source': 'inventory_delta|bank_delta|menu_action', 'confirmationLevel': 'bank_container_delta_confirmed'}]`

## Traversal Profile
- Recordings: `3`
- Target-relative clicks: `11`
- Strong/medium rate: `1.0`
- Menu selections: `10`
- Imperfect successful clicks: `10`

## Menu Profile
- Recordings: `3`
- Target-relative clicks: `11`
- Strong/medium rate: `1.0`
- Menu selections: `10`
- Imperfect successful clicks: `10`

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
- missing: target_match_quality

## Script-Facing Recommendations
- Prefer strong/medium target-quality evidence over exact clickbox-center replication.
- Allow target-relative variance; outside recovered geometry can still be successful when menu or postcondition evidence proves the action.
- Preserve hover/menu context because it explains many human menu selections and slightly messy clicks.
- Treat camera adjustment as useful pre-action evidence, especially for route transitions and tree visibility.
- Use routeSegments/world/plane for route proof instead of raw click counts.
- Use bank_ui and bank container delta when judging banking; input region labels alone can be misleading.
