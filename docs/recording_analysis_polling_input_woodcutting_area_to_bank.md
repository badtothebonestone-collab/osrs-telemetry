# Polling Input Route Recording Analysis

Recording inspected:

`C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area`

Note: no folder exactly matching `manual_action-woodcuting_area_to_bank_polling_input` was found under the usual recording roots. The newest route recording with polling input enabled is the folder above. Its manifest label is `manual_action-Bank to Woodcutting area`, so this is the reverse route: bank/post-deposit area to woodcutting area.

Analyzer command run:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_003927_manual_action-Bank_to_Woodcutting_area" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping
```

## Verdict

Polling input capture solved the missing OS input trace problem.

The previous route recording had only `capture_start` and `capture_stop`. This recording captured a real OS input stream:

- Backend requested: `polling`
- Backend used: `polling`
- Input status: `PASS`
- Capture status: `captured_real_input`
- Input events: `400`
- Real input events: `398`
- Mouse moves: `328`
- Mouse downs / ups / clicks: `7 / 7 / 7`
- Keyboard events: `30`
- Foreground window samples: mostly RuneLite, `366` events on `RuneLite - KCLBolus`; `34` events on `OSRS Telemetry Control`

The recording now contains a usable synchronized dataset:

- Game telemetry: yes
- OS input trace: yes
- Joined input-to-telemetry rows: yes
- Camera behavior: yes
- Target-relative click candidates: yes, but needs filtering/quality improvement
- Arduino status/mapping: partial; VM mapping works, Arduino action-path commands did not

## Recording Summary

| Field | Value |
|---|---:|
| Duration | `48.25s` |
| Snapshots | `16` |
| Tick range | `57 -> 128` |
| Parse failures | `0` |
| Hover samples | `16` |
| Open menu samples | `0` |
| Joined clicks | `7` |
| Target-relative clicks | `7` |
| Camera segments | `1` |
| Camera-before-click count | `0` |
| VM mapping status | `PASS` |

Input preflight passed before the recording:

- Success: `true`
- Reason: `captured mouse movement and click/button activity`
- Preflight events: `217`
- Preflight real events: `215`
- Preflight moves: `177`
- Preflight downs/clicks: `2 / 2`
- Preflight key downs/ups: `4 / 4`

## Route Timeline

The route telemetry shows a successful bank-floor-to-ground-floor traversal and movement toward the woodcutting area:

| Time | Tick | World position |
|---:|---:|---|
| `2.563s` | `57` | `3206,3226,2` |
| `3.547s` | `58` | `3206,3228,2` |
| `4.469s` | `60` | `3206,3229,2` |
| `6.281s` | `64` | `3205,3228,1` |
| `9.594s` | `67` | `3205,3228,0` |
| `19.016s` | `79` | `3211,3228,0` |
| `24.125s` | `85` | `3215,3218,0` |
| `28.078s` | `94` | `3203,3214,0` |
| `31.625s` | `101` | `3198,3222,0` |
| `36.906s` | `106` | `3199,3234,0` |
| `40.813s` | `115` | `3198,3243,0` |

Plane changes:

- Tick `64`: plane `2 -> 1` at `3205,3228`
- Tick `67`: plane `1 -> 0` at `3205,3228`

Traversal object evidence:

- `Staircase`, id `56231`, actions `Climb-down`, `Bottom-floor`, seen near tick `57`, plane `2`
- `Staircase`, id `56230`, actions `Climb-up`, `Top-floor`, seen on plane `0`
- `Ladder`, ids including `16679` / `16683`, actions including `Climb-down` / `Climb-up`

Route/traversal verdict: strong. The recording confirms two floor transitions and movement from the bank area toward the woodcutting area.

## Click/Join Evidence

The OS input trace captured 7 click events and the joiner produced 7 target-relative click candidates.

| Time | Tick | Button | Joined target | Id | Actions | Note |
|---:|---:|---|---|---:|---|---|
| `5.344s` | `60` | right | Staircase | `56231` | `Climb-down`, `Bottom-floor` | plausible traversal interaction |
| `6.469s` | `64` | left | Door | `1535` | `Open` | plausible nearby object, after plane change |
| `12.266s` | `67` | left | Ladder | `16683` | `Climb-up` | likely route/candidate match, but user intent may have been movement/camera |
| `16.906s` | `70` | middle | Staircase | `56230` | `Climb-up`, `Top-floor` | should be classified as camera input, not action click |
| `23.797s` | `79` | middle | Ladder | `16683` | `Climb-up` | should be classified as camera input, not action click |
| `35.031s` | `101` | left | Staircase | `56230` | `Climb-up`, `Top-floor` | candidate match, needs intent review |
| `42.031s` | `115` | left | Ladder | `16683` | `Climb-up` | foreground was `OSRS Telemetry Control`; likely UI/control noise |

Target-relative click verdict: useful but not clean enough yet. The joiner proves synchronization is working, but it currently counts middle-button camera drags and at least one UI/control click as target-relative game clicks. Relative click distances are also large because coordinate-space handling still needs tightening.

## Camera Behavior

Camera behavior was captured and synchronized:

- Status: `PASS`
- Camera segments: `1`
- Source: `middle_mouse_drag`
- Duration: `29.859s`
- Yaw delta: `1638`
- Pitch delta: `79`
- Mouse drag: start `1043,449`, end `1309,455`, delta `266,6`
- Camera-before-click count: `0`

Camera verdict: strong enough for route-analysis evidence. The middle-button drag was detected as camera movement, but the same middle-button release also appears in click joining and should be filtered out of action-click analysis.

## Arduino And VM Mapping

Arduino trace:

- Status: `WARN`
- Classification: `arduino_bridge_connected`
- Port: `COM6`
- Events: `3`
- Errors: `1`
- Commands: `0`
- Health/status commands: `0`
- Action commands: `0`
- Movement commands: `0`
- Click commands: `0`
- Per-action HID evidence: `false`

Arduino error:

`ArduinoHIDError: Arduino serial port COM6 is locked by osrs-telemetry:8936`

VM mapping:

- Status: `PASS`
- Input events mapped: `400`
- Mouse path point count: `346`
- Relative segment count: `532`
- Total dx/dy: `-809`, `248`
- Click mappings: `7`

Arduino verdict: VM mouse mapping now works because OS input exists. Arduino bridge evidence is not action-path evidence in this recording because the serial port was locked and no movement/click commands were captured.

## Bank Target Verdict

This recording starts after the bank deposit, so it is not a clean woodcutting-area-to-bank bank-click validation.

Bank objects were present at the start:

- `Bank booth`, id `27291`, world point `3209,3221,2`, distance `5`, on screen `true`
- `Bank booth`, id `18491`, world point `3208,3221,2`, distance `5`, on screen `true`
- `Bank table`, id `590`, world point `3207,3215,2`

But no OS click was joined to a bank target, and the manifest says the action started after log deposit. Bank target verdict: bank object identity is present, but this recording does not validate a bank-click/open-bank action.

## Schema Gap Summary

Present and useful:

- Tick/export sequence/freshness
- Player world/local point and plane
- Inventory slots
- Nearby and route objects
- Effective object names/actions
- Stable refs and distances
- Canvas/clickbox/aim geometry
- Hover entries
- Camera/window metadata
- OS input trace
- Camera behavior
- VM mouse mapping

Still missing or weak:

- `destination`
- `bank_container`
- `selected_item_spell_widget_state`
- `bank_state`
- `equipment_slots`
- `top_level_interface_widget_state`

The biggest remaining practical gap for this dataset is not another bridge field. It is analyzer/context shaping for input event quality:

- Filter out non-RuneLite foreground input events before action-click joining.
- Exclude middle-button drag/releases from action-click target analysis.
- Separate `game_action_clicks`, `camera_input`, and `ui_control_clicks`.
- Improve coordinate-space quality for target-relative clicks, especially screen/client/canvas normalization.

## Recommended Next Implementation Task

Implement an input action-click classifier in Python.

Suggested scope:

1. In `input_trace_joiner.py`, classify input events as:
   - `game_action_click`
   - `camera_drag`
   - `ui_control_click`
   - `keyboard_navigation`
   - `ambiguous_input`
2. Only run target-relative object matching for RuneLite foreground viewport left/right clicks.
3. Exclude middle-button camera drag click artifacts from `targetRelativeClickCount`.
4. Report:
   - total OS clicks
   - game action clicks
   - camera drag clicks
   - UI/control clicks
   - target-relative action clicks
   - coordinate quality warnings
5. Prefer client/canvas coordinates consistently when comparing OS clicks to telemetry aim/clickbox geometry.
6. Update `summary.json`, `schema_gap_report.md`, `telemetry_ui.py` status text, and tests.

Validation recording:

- Record the original direction again: woodcutting area to bank.
- Start at the tree area with inventory/log state known.
- Click normal movement tiles, interact with stairs/ladder, then click/open the bank booth.
- Keep polling input, input preflight, camera behavior, join input, VM mapping, and Arduino bridge enabled.

Expected successful result:

- Polling input remains `PASS`.
- OS action clicks are counted separately from camera/UI clicks.
- Stairs/ladder clicks are joined to traversal objects.
- Bank click is joined to `Bank booth` with `Bank` action.
- VM mapping remains `PASS`.
- Arduino either reports `arduino_status_only` cleanly or captures real action commands if bridge control is intentionally enabled.
