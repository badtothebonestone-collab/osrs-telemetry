# Live Mirror Menu-Row Validation Analysis

## Verdict

**WARN: the stack mostly worked, but this recording does not fully prove live mirrored menu-click actions.**

The recording proves fresh telemetry, polling input capture, input classification, target quality, Arduino live mirror movement commands, and click-storm protection. It does **not** prove row-geometry hit testing or Arduino-mirrored menu-selection clicks.

The key issue is timing: live mirror armed for a short test window, emitted movement commands, then disarmed before the actual menu-selection clicks. The menu clicks were captured by OS polling and classified correctly, but no Arduino `CLICK` commands were sent for those menu actions.

## Recording

Recording folder:

`C:\Users\badto\osrs-telemetry\recordings\20260603_082120_manual_action-menu_row_validation_live_mirror_controlled`

Analyzer command run:

```powershell
python telemetry-viewer\analyze_manual_recording.py "C:\Users\badto\osrs-telemetry\recordings\20260603_082120_manual_action-menu_row_validation_live_mirror_controlled" --summary --schema-gap --input-trace --join-input --camera-behavior --arduino-trace --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --print-input-path-integrity --print-coordinate-alignment --print-menu-interactions --print-target-match-quality
```

Analyzer result: succeeded.

## Timeline Summary

| Field | Result |
| --- | --- |
| Created UTC | `2026-06-03T13:21:20.662681Z` |
| Completed UTC | `2026-06-03T13:21:49.338195Z` |
| Duration | `27.625s` |
| Snapshots | `18` |
| Tick range | `82 -> 112` |
| Parse failures | `0` |

## Core Layer Results

| Layer | Verdict | Evidence |
| --- | --- | --- |
| Telemetry freshness | PASS | Main live sources were fresh, mostly around `0.35s-0.42s` old. Candidate/overlay debug sources were around `2.247s` old with stale observations. |
| Polling input capture | PASS | `157` input events, `155` real input events, backend requested/used `polling/polling`. |
| Coordinate alignment | WARN | Chosen transform was `client_inverse_dpi_1_75`, but menu click points still tested outside row bounds. |
| Menu row geometry | WARN | Menu selections were linked to targets, but `menuSelectionsWithRowGeometryCount` was `0` and `menuSelectionsMissingRowGeometryCount` was `2`. |
| Input action classification | PASS | `5` raw OS clicks, `2` eligible game-action clicks, both classified as `menu_selection_click`. |
| Target match quality | PASS with warnings | `2` target-relative clicks, both strong matches, both target `Staircase`, action `Climb-down`. |
| Live Arduino mirror command stream | WARN | `16` non-probe live mirror commands were sent, but they were movement-only and occurred before the menu selections. |
| Click-storm protection | PASS | `0` Arduino click commands, max Arduino commands/sec `4`, max click commands/sec `0`, no panic stop. |

## Input Capture

| Field | Result |
| --- | --- |
| Input backend requested | `polling` |
| Input backend used | `polling` |
| Input status | `PASS` |
| Input capture status | `captured_real_input` |
| Total input events | `157` |
| Real input events | `155` |
| Mouse moves | `125` |
| Raw OS clicks | `5` |
| Mouse downs | `5` |
| Mouse ups | `5` |
| Drag starts | `3` |
| Drag moves | `5` |
| Drag ends | `3` |
| Key downs | `2` |
| Key ups | `2` |

## Input Action Classification

| Field | Result |
| --- | --- |
| Status | `PASS` |
| Raw OS clicks | `5` |
| Eligible game-action clicks | `2` |
| Target-relative clicks | `2` |
| Menu opens | `0` classified right-click menu opens |
| Menu selections | `2` |
| UI/control clicks | `1` |
| Ambiguous clicks | `2` |
| Excluded clicks | `3` |

Classification counts:

| Classification | Count |
| --- | --- |
| `menu_selection_click` | `2` |
| `ambiguous_click` | `2` |
| `ui_control_click` | `1` |

The two retained game actions were:

| Event seq | Classification | Option | Target |
| --- | --- | --- | --- |
| `83` | `menu_selection_click` | `Climb-down` | `Staircase` |
| `109` | `menu_selection_click` | `Climb-down` | `Staircase` |

## Menu Interaction Analysis

| Field | Result |
| --- | --- |
| Status | `WARN` |
| Right-click menu opens | `0` |
| Menu selections | `2` |
| Rows resolved | `2` |
| Selections linked to targets | `2` |
| Selections with row geometry | `0` |
| Selections missing row geometry | `2` |

Selected rows:

| Event seq | Selected row | Selected option | Selected target | Row geometry proven | Linked game target |
| --- | --- | --- | --- | --- | --- |
| `83` | `0` | `Climb-down` | `Staircase` | no | object `Staircase`, id `56231`, world `3205,3229,2` |
| `109` | `0` | `Climb-down` | `Staircase` | no | object `Staircase`, id `56231`, world `3205,3208,2` |

Menu telemetry preserved enough state to resolve the selected target/action, but the row hit test did not prove the clicked row from geometry. The coordinate alignment examples show menu bounds and row counts, but the transformed click points were still outside menu bounds.

## Coordinate Alignment

| Field | Result |
| --- | --- |
| Status | `WARN` |
| Chosen transform | `client_inverse_dpi_1_75` |
| Detected DPI scale | `1.6258` |
| Menu selection candidates | `2` |
| Raw menu-row hits | `0` |
| Normalized menu-row hits | `0` |
| Hit-test success by transform | `client_inverse_dpi_1_75: 2` candidate transform matches |

Examples:

| Event seq | Raw client point | Normalized point | Result |
| --- | --- | --- | --- |
| `83` | `710,129` | `405.714,73.714` | point outside menu bounds |
| `109` | `692,170` | `395.429,97.143` | point outside menu bounds |

This means coordinate transform inference is active, but row geometry is not yet proven for this fixture.

## Target Match Quality

| Field | Result |
| --- | --- |
| Status | `PASS` |
| Target-relative clicks | `2` |
| Strong matches | `2` |
| Medium matches | `0` |
| Weak matches | `0` |
| Unmatched | `0` |

Per target:

| Event seq | Target | Action | Quality | Score | Main evidence | Warnings |
| --- | --- | --- | --- | --- | --- | --- |
| `83` | `Staircase` | `Climb-down` | strong | `1.0` | menu row option/target, object id/ref/action, fresh telemetry, post-click position change | menu row geometry missing, target not on screen, no plane change in window |
| `109` | `Staircase` | `Climb-down` | strong | `1.0` | target on screen, hover/menu confirmation, object id/ref/action, expected post-click outcome | menu row geometry missing |

Target quality is strong because the logical target/action evidence is good. It should not be read as proof that the menu row click point itself landed inside a row.

## Live Arduino Mirror

| Field | Result |
| --- | --- |
| Input path classification | `arduino_mirror_verified` |
| Arduino port | `COM6` |
| Protocol | `arduino_hid.v1` |
| Arduino connected | `true` |
| Probe classification | `arduino_probe_verified_clean` |
| liveMirrorActive | `true` |
| liveMirrorVerified | `true` |
| possibleDoubleInput | `false` |
| Total Arduino commands | `17` |
| Probe commands | `1` |
| Non-probe Arduino commands | `16` |
| Movement commands | `17` including probe, `16` non-probe |
| Click commands | `0` |
| Keyboard commands | `0` |
| Ack count | `17` |
| Correlated movement commands | `16` |
| Correlated click commands | `0` |
| Uncorrelated OS moves | `114` |
| Uncorrelated OS clicks | `5` |
| Uncorrelated Arduino commands | `1` |

The live mirror sent non-probe movement commands from early input events and those movement commands correlated with observed cursor movement. That is enough for `arduino_mirror_verified` on movement, but not enough to prove mirrored menu-selection clicks.

The menu-selection clicks happened later:

| Event seq | Elapsed seconds | Action |
| --- | --- | --- |
| `83` | about `13.141s` | `Climb-down Staircase` menu selection |
| `109` | about `18.125s` | `Climb-down Staircase` menu selection |

The live mirror summary shows the mirror test window was `5.0s`, and `144` later events were dropped with reason `mirror_disarmed`. So the mirror was no longer armed when the menu-selection clicks happened.

## Persistent Arm Diagnosis

| Field | Result |
| --- | --- |
| Arm mode used by fixture | `test_window` |
| Mirror armed start | `6.063s` elapsed |
| Mirror disarm time | `11.063s` elapsed |
| Disarm reason | `test_window_elapsed` |
| Test duration | `5.0s` |
| Recording-persistent arm present | no |
| First game action | `13.141s` elapsed |
| First menu selection | `13.141s` elapsed |
| Menu selections after disarm | `2` |
| Action clicks after disarm | `2` |
| Target actions after disarm | `2` |
| Final mirror recording verdict | `WARN` |

The rerun now explicitly classifies this as a recording arm-mode problem:

- `test_window_used_for_recording`
- `menu_actions_after_mirror_disarm`
- `clicks_after_mirror_disarm`
- `target_actions_after_mirror_disarm`
- `recording_persistent_arm_missing`

Action timing:

| Event seq | Classification | Target/action | Elapsed | Time since disarm | Mirror armed at action | Mirrored action likely |
| --- | --- | --- | --- | --- | --- | --- |
| `83` | `menu_selection_click` | `Staircase / Climb-down` | `13.141s` | `2078ms` | no | no |
| `109` | `menu_selection_click` | `Staircase / Climb-down` | `18.125s` | `7062ms` | no | no |

This is why the recording remains `WARN`: live mirror movement was verified early, but the actual menu actions happened after the mirror had already disarmed. It is not valid proof of mirrored menu clicks.

The fixed next recording should show:

- `armMode: recording_persistent`
- `recordingPersistent: true`
- no `test_window_used_for_recording` warning
- no disarm before the recording stop
- `menuSelectionsAfterDisarm: 0`
- `actionClicksAfterDisarm: 0`
- non-probe Arduino `CLICK` command count greater than `0`
- correlated click count greater than `0`

## Click-Storm Protection

| Field | Result |
| --- | --- |
| Max Arduino commands/sec | `4` |
| Max click commands/sec | `0` |
| Dropped commands | `144` events dropped after disarm |
| Throttled commands | `0` |
| Duplicate source events | `13` from chunked movement commands |
| Panic stops | `0` |
| Safety classifications | none |
| UI/control events | UI click classified as `ui_control_click` |

The click storm did **not** happen again. The controlled mirror behavior stayed bounded. However, the test also did not exercise live mirrored click output because the mirror disarmed before the menu clicks.

## Biggest Remaining Gap

The biggest gap is that the controlled recording used the live mirror test arming window for a real recording. The mirror disarmed after about five seconds, before the actual menu-row actions at about 13 and 18 seconds.

That leaves two things unproven:

1. Whether menu-selection clicks can be sent as live Arduino `CLICK` commands during a real interaction.
2. Whether row geometry can prove the clicked menu row, rather than linking the action through target/menu evidence alone.

## Next Recommended Task

Rerun the same menu-row validation recording with persistent mirror arming enabled and check for:

- non-probe Arduino `CLICK` commands for event `83` / `109` equivalents
- correlated click count greater than `0`
- no click storm
- row geometry hit test success greater than `0`

Recommended next validation label:

`manual_action-menu_row_validation_live_mirror_persistent_arm`
