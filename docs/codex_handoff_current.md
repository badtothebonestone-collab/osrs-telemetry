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
- Bank UI / Service State Context v1 is implemented in the plugin snapshot,
  bank UI analyzer, task phase integration, daemon status fields, and
  `diagnose_bank_ui_context.py`.
- Stabilization suite passes.
- Bank UI / Service State Context v1 works.
- Bank Operation Context v1 works.
- Bank operation reports operationNeeded, operationType, resourceItemsHeld, resourceItemSlots, resourceItemQuantity, nonResourceItemsHeld, inventoryFreeSlots, depositInventoryAvailable, depositWouldClearResourceInventory, bankingComplete, and completionReason.
- With bank open after logs are deposited: bankOperation PASS, operationNeeded=no, operationType=none, resourceItemsHeld=0, bankingComplete=yes, completionReason=no_resource_items_held.
- Previous Bank Operation baseline after deposit was phase=service_complete,
  activeIntent=resume_resource_collection; Return-to-Resource Context v1 now
  advances that state back toward resource selection when free slots and target
  context are available.
- Return-to-Resource Context v1 is implemented in Python analyzers,
  daemon status fields, task transition diagnostics, intent overlay behavior,
  and `diagnose_return_to_resource_context.py`.
- After bankingComplete=true with free inventory slots and a visible resource
  target, woodcut_bank transitions back to resource targeting:
  phase=target_selected, activeIntent=select_target.
- After bankingComplete=true with no visible resource target, woodcut_bank
  reports needs_more_context/select_target for resource target context rather
  than keeping bank service intent active.

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

python telemetry-viewer\diagnose_return_to_resource_context.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes

## Verification commands

python telemetry-viewer\run_stabilization_suite.py

.\gradlew.bat test

.\gradlew.bat build

## Current next milestone

Return-to-Resource Context v1 live QA.

Goal:
bankingComplete=true -> free inventory slots -> resource target selection and
resource overlay/pathing resume without keeping bank service path as the active
intent.

Implemented scope:
- `return_to_resource_analyzer.py` reports returnNeeded, returnReady,
  serviceComplete, resourceTargetAvailable, bestResourceTarget,
  resourcePathingNeeded, inventory free/full state, warnings, and missing
  capabilities.
- `diagnose_return_to_resource_context.py` reports daemon return-to-resource
  context in human and JSON modes.
- Task phase integration:
  - bankingComplete + free slots + resource target visible -> target_selected /
    select_target
  - bankingComplete + free slots + no resource target -> needs_more_context /
    select_target
- Intent overlay suppresses stale completed bank service path markers during
  return-to-resource and selects the resource target when available.
- Do not change pathing, service ranking, service memory, or plugin telemetry
  unless the return-to-resource work truly requires it.

## Live QA / Computer Use workflow

Codex may run terminal commands needed for live QA.

Preferred live QA flow:
1. Launch RuneLite dev when needed:
   .\gradlew.bat run
2. Wait for the RuneLite dev client to open.
3. If RuneLite requires user login or account confirmation, stop and ask the user to handle it.
4. Once the user is logged in and the plugin endpoint is available, continue automatically.
5. Wait until plugin snapshot endpoint reports LOGGED_IN.
6. Start/restart live_core_daemon.py.
7. Run live diagnostics:
   python telemetry-viewer\diagnose_service_context.py --from-daemon --daemon-url http://127.0.0.1:8890
   python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890
   python telemetry-viewer\diagnose_overlay_state.py --latest-session --intent
   python telemetry-viewer\diagnose_return_to_resource_context.py --from-daemon --daemon-url http://127.0.0.1:8890
   python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank
   python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes

If Codex Computer Use can operate the RuneLite dev window, it may click simple already-authenticated buttons such as Play / Log in / Continue, but it should not handle credentials or account settings. If Computer Use cannot access the RuneLite window, ask the user to click/log in manually, then continue with endpoint and diagnostics.

For preferred window placement:
- If the RuneLite dev client opens on the wrong monitor, try moving the active window to the other monitor with Windows+Shift+Left or Windows+Shift+Right.
- If this cannot be done reliably, ask the user to move the window manually.

Useful endpoint check:
$request = @{
  schema = "plugin_snapshot_request.v1"
  needs = @("baseline", "writer_health")
  maxAgeTicks = 5
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/snapshot" -Body $request -ContentType "application/json"
