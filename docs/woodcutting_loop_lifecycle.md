# Woodcutting Loop Lifecycle

`woodcutting_loop_lifecycle.json` is a compact task-state summary for the full
woodcutting loop. It does not add raw telemetry. It combines existing analyzer
artifacts into one script-readable answer:

- current loop state
- current phase
- next expected phase
- evidence that supports the phase
- warnings and missing capabilities

## Inputs

The loop lifecycle consumes whichever of these artifacts are present:

- `woodcutting_lifecycle.json`
- `banking_lifecycle.json`
- `traversal_lifecycle.json`
- `route_template_comparison.json`
- `route_monitor_status.json`
- `route_history_summary.json`
- `interruption_lifecycle.json`
- `combat_damage_summary.json`
- `human_click_profile.json`
- `summary.json`

It is valid for a recording to contain only one part of the loop, such as
woodcutting-only, route-only, banking-only, interrupted woodcutting, or a full
loop.

## Phases

The implemented phases are:

- `at_trees`
- `cutting`
- `inventory_full`
- `inventory_near_full`
- `routing_to_bank`
- `banking`
- `deposit_complete`
- `routing_to_trees`
- `resumed_cutting`
- `interrupted`
- `complete`

## Next Expected Phase

Examples:

- Woodcutting ended full: `route_to_bank`
- Woodcutting ended near full: `continue_cutting`
- Route ended at `bank_area`: `banking_deposit`
- Deposit complete: `route_to_woodcutting_area`
- Route ended at `woodcutting_area`: `resume_cutting`
- Interruption without resume: `recover_or_resume_task`
- Interruption with resume: `continue_current_phase`

## Script Use

Task scripts should use `task_script_api.py` helpers instead of parsing the raw
JSON:

```python
import task_script_api as api

loop = api.get_woodcutting_loop_lifecycle(recording_folder)
next_phase = api.get_next_expected_phase(recording_folder)

if api.should_route_to_bank(recording_folder):
    ...
```

## CLI

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --woodcutting-loop-lifecycle --print-woodcutting-loop-lifecycle
```

## Current Gap

The loop analyzer is proven across separate recordings. A single clean fixture
covering chop to full inventory, route to bank, deposit, route back, and resumed
chopping is still useful.
