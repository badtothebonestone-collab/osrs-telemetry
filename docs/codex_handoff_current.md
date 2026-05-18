# Current Codex Handoff

Repo:
C:\Users\stone\osrs-telemetry\example-plugin

Use this file as a compact current-state handoff for new Codex chats. Do not treat older chat history as source of truth. Use current repo files, tests, and diagnostics.

## Daily command

python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark

## Current architecture

RuneLite plugin
-> plugin snapshot endpoint
-> live_core_daemon.py
-> analyzers
-> Mission Control / diagnostics / overlay

## Current working baseline

- Snapshot No-File is the daily path.
- No continuous runtime JSON/NDJSON spam.
- Scanner/checker/filter field-name systems were removed and should not return.
- Do not strip useful telemetry fields because of names like actions, menuActions, clickbox, target, path, interaction, etc.
- Filtering should only happen for explicit performance, size, display, or task-selection reasons.
- Service selection works.
- Bank booth wins over Deposit Box when visible.
- Retained Bank booth blocks Deposit Box fallback when current candidates drop the booth.
- Collision window cache works.
- OSRS-like predicted path mode works.
- Tile overlay shows predicted path.
- Path integrity validation works.
- Path intent stabilization works.
- Arrival/serviceReady layer was added.
- Stabilization suite passes.

## Current service-memory proof

Known-good service behavior:
- When Bank booth is visible, Bank booth is selected.
- When Bank booth drops from current candidates, retained Bank booth blocks Deposit Box fallback.
- Deposit Box should remain fallback for woodcut_bank, not default.

## Useful diagnostics

python telemetry-viewer\diagnose_service_context.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\diagnose_overlay_state.py --latest-session --intent

python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank

python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes

## Verification commands

python telemetry-viewer\run_stabilization_suite.py

.\gradlew.bat test

.\gradlew.bat build

## Current next milestone

Bank UI / Service State Context v1.

Goal:
serviceReady -> bank UI observed -> bankOpen / bankReadable / bankPinOpen / inventory and bank summaries.

Scope:
- Add bank UI telemetry if needed.
- Add bank_ui_analyzer.py.
- Add diagnose_bank_ui_context.py.
- Integrate task phase:
  - serviceReady + bankOpen=false -> service_available
  - bankOpen=true + bankReadable=true -> service_open
  - bankPinOpen=true -> blocked / bank_pin_required
- Do not change pathing, service ranking, service memory, or path overlay unless the Bank UI work truly requires it.
