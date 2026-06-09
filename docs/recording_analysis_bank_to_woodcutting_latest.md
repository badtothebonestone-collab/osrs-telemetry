# Latest Bank to Woodcutting Area Recording

Generated: 2026-06-07

## Recording

- Folder: `C:\Users\badto\osrs-telemetry\recordings\20260606_201613_Bank_to_tree_area`
- Label: `Bank to tree area`
- Duration: 28.454 seconds
- Tick range: 23 to 63
- Parse failures: 0
- Telemetry freshness during recording: good; no stale sources reported in `summary.json`
- UI manifest: `ui_recording_session_manifest.json` was present in the UI control folder and was backfilled into the recording folder after analysis.

## Verdict

- Traversal lifecycle: PASS
- Detected route: `Bank_to_Woodcutting_area`
- Route template: `Bank_to_Woodcutting_area`, revision 3
- Template comparison: PASS_BASE_TEMPLATE
- Template score: 1.0
- Required segments matched: 5 / 5
- Missing required segments: 0
- Failed postconditions: 0
- Direction mismatch: false
- Route monitor: PASS, arrived
- Route history: PASS, arrived
- Off route: false

## Route Evidence

Start:
- Area: `bank_area`
- World: 3206, 3215, plane 2

End:
- Area: `woodcutting_area`
- World: 3195, 3243, plane 0

Movement:
- Approximate traversal distance: 45.495 tiles from traversal lifecycle
- Plane changes: 1
- Plane transition: 3205,3209,2 to 3206,3208,0

Route segments:

1. `area_start`: Start: bank_area, success
2. `walk_segment`: Walk, success
3. `stair_transition`: Climb-down Staircase, success
4. `walk_segment`: Walk, success
5. `walk_segment`: Walk here Door, optional/navigation support, success
6. `stair_transition`: Cancel Staircase, allowed review evidence, partial
7. `area_arrival`: Arrive: woodcutting_area, success

The required template segments were all satisfied. The extra Cancel Staircase evidence was treated as review/incidental evidence and did not weaken the route comparison.

## Input And Menu Evidence

- Raw OS clicks: 5
- Eligible game-action clicks: 5
- Menu selections: 5
- Menu selections linked to targets: 5
- Menu selections with row geometry: 4 / 5
- Target quality: strong for the route-relevant menu selections
- Coordinate transform used for menu rows included `client_inverse_dpi_1_75`
- Duplicate click likely count: 0
- Arduino live click commands: 0

The one missing row geometry item was the Climb-down Staircase selection. It still linked strongly to the target and was confirmed by the plane-change postcondition, so it is not a route data problem.

## Useful Data

This recording is useful as a Bank to Woodcutting Area route sample.

Strong evidence:
- Fresh telemetry.
- Clean input capture.
- Route start and end areas matched the template.
- Required route segment sequence matched.
- Plane transition lined up with the stair segment.
- Route monitor and offline analyzer both concluded `arrived`.
- No duplicate live Arduino click issue.

Remaining caveats:
- Arduino artifacts are absent or empty because Arduino was not part of this recording. This is expected and not a route failure.
- Raw Input device attribution was unavailable in polling mode, but polling input capture was valid.
- The UI manifest was not originally written into the recording folder; this was a UI breadcrumb issue, not a data-capture issue. The UI now mirrors future manifests into the actual recording folder.

## Fix Made

Updated `telemetry-viewer\telemetry_ui.py` so `update_ui_recording_manifest()` mirrors `ui_recording_session_manifest.json` into the actual recording folder once `recordingFolder` is known.

Added a regression test in `telemetry-viewer\tests\test_telemetry_ui.py`.

## Checks

- `python -m py_compile telemetry-viewer\telemetry_ui.py telemetry-viewer\manual_recorder.py telemetry-viewer\analyze_manual_recording.py`
- `python telemetry-viewer\tests\test_telemetry_ui.py`
- `python telemetry-viewer\telemetry_ui.py --check`

All checks passed.
