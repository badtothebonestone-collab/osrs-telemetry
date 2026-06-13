# Project State

Updated: `2026-06-13T20:04:44.970494Z`

## Current Architecture

RuneLite plugin / bridge exports read-only live telemetry through the snapshot
endpoint. Record Everything preserves useful live packets into recordings.
Analyzer modules convert those recordings into lifecycle and quality artifacts.
`context_service.py`, MCP wrappers, and `task_script_api.py` expose compact,
script-readable summaries.

## Stable Workflow

1. Open OSRS Telemetry Recorder.
2. Start Game.
3. Start Telemetry.
4. Start Recording.
5. Do the task normally.
6. Stop Recording.
7. Let automatic analysis finish.
8. Read the summary or open the output folder.

## What Not To Rebuild

- Do not replace Record Everything with per-task recording knobs.
- Do not create a second live input API.
- Do not make scripts parse raw recording JSON when context/task APIs expose
  compact fields.
- Do not treat route raw clicks as required template progress; use routeSegments.

## Main Checks

```powershell
python telemetry-viewer\update_project_knowledge.py --check
python telemetry-viewer\telemetry_ui.py --check
python telemetry-viewer\tests\test_project_knowledge.py
```

## Manual Notes

<!-- BEGIN MANUAL NOTES -->
<!-- END MANUAL NOTES -->
