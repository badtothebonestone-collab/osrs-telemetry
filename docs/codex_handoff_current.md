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
- Post-bank World Reacquisition Context v1 distinguishes bank-complete states:
  bank UI still open defers resource targeting as waiting_for_world_view /
  wait_for_world_view, bank closed allows resource targeting to resume, and
  missing resource targets are only treated as target context after the world
  view is available.
- Close-bank Readiness / Return Control Context v1 reports closeBankNeeded,
  closeBankReady, close button visibility/availability, keyboardClosePossible,
  and the close_service_context intent while banking is complete but the bank UI
  is still open.
- Full Woodcut Bank Cycle QA Harness v1 summarizes the whole woodcut_bank cycle
  in one stdout-only diagnostic with PASS/WARN/FAIL status and compact stage,
  inventory, service, pathing, bank, close-bank, post-bank, return, and overlay
  fields.
- Cycle History / State Transition Trace v1 keeps a compact rolling in-memory
  history of meaningful woodcut_bank stage/context changes and exposes a small
  status tail plus `diagnose_cycle_history.py`.
- Resource Return Destination / Resource Area Memory v1 remembers the last
  productive woodcutting area while resources are visible and inventory has
  space. After bankingComplete=true and bankOpen=false, if no resource target
  is currently visible, `resourceReturnContext` can provide a remembered
  resource-return destination and the task can use return_to_resource /
  return_to_resource_area instead of treating missing tree candidates as an
  immediate resource-target failure.

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

python telemetry-viewer\diagnose_post_bank_reacquisition_context.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\diagnose_close_bank_context.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20

python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890

python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes

## Verification commands

python telemetry-viewer\run_stabilization_suite.py

.\gradlew.bat test

.\gradlew.bat build

## Current next milestone

Resource Return Destination / Resource Area Memory v1 live QA with the daily
daemon.

Goal:
Confirm that the daemon remembers the last productive woodcutting area before
banking, then after bankingComplete=true and bankOpen=false uses that remembered
area as a resource-return destination when no tree target is currently visible.

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
- `post_bank_reacquisition_analyzer.py` reports whether resource target
  reacquisition is deferred by the open bank UI or allowed after the bank closes.
- `diagnose_post_bank_reacquisition_context.py` reports daemon post-bank
  reacquisition state in human and JSON modes.
- `close_bank_analyzer.py` reports whether closing the bank UI is needed and
  ready, using close button telemetry or keyboardClosePossible when available.
- `diagnose_close_bank_context.py` reports daemon close-bank state in human and
  JSON modes.
- `diagnose_woodcut_bank_cycle.py` reports the full live woodcut_bank cycle in
  human and JSON modes.
- `diagnose_cycle_history.py` reports the rolling in-memory cycle transition
  tail in human and JSON modes. It writes no files.
- `resource_return_analyzer.py` keeps in-memory resource-area memory and reports
  `resourceReturnContext` with destination needed/available, destination tile,
  destination source, memory validity, age, visible-target state, reason,
  warnings, and missing capabilities.
- `diagnose_resource_return_context.py` reports daemon resource-return context
  in human and JSON modes. It writes no files.
- Task phase integration:
  - bankingComplete + bankOpen=true -> waiting_for_world_view /
    close_service_context, no target candidate failure
  - bankingComplete + bankOpen=false + resource target visible ->
    target_selected / select_target
  - bankingComplete + bankOpen=false + no resource target + valid remembered
    resource area -> return_to_resource / return_to_resource_area
  - bankingComplete + bankOpen=false + no resource target + no resource memory
    -> needs_more_context / select_target
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
   python telemetry-viewer\diagnose_post_bank_reacquisition_context.py --from-daemon --daemon-url http://127.0.0.1:8890
   python telemetry-viewer\diagnose_close_bank_context.py --from-daemon --daemon-url http://127.0.0.1:8890
   python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890
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


## Current Completed Milestone: Bank Operation + Return-to-Resource + Post-bank + Close-bank

Bank Operation Context v1 works.

Return-to-Resource Context v1 works.

Post-bank World Reacquisition Context v1 works in analyzers, daemon phase
integration, diagnostics, overlay suppression, and daily gauntlet deferral.

Close-bank Readiness / Return Control Context v1 works in Python analyzers,
daemon phase integration, diagnostics, status fields, and daily gauntlet
deferral. Java bank UI telemetry now also reports keyboardClosePossible when a
top-level bank UI is open.

After depositing logs with bank open:
- bankOperation PASS
- bankingComplete=true
- resourceItemsHeld=0
- phase=needs_more_context or return_to_resource depending visible resource target
- activeIntent=select_target / resume_resource_collection
- if bank UI is still open, phase=waiting_for_world_view and
  activeIntent=close_service_context

Open-bank view with no visible tree target can produce missing target candidates/freshness. This should be treated as expected when the bank UI is still open and the world/resource target view is deferred, not as a bank/service failure.
Post-bank reacquisition now represents that state explicitly with
reason=bank_ui_still_open and resourceTargetReacquisitionAllowed=false.

## Current Next Milestone: Resource Return Destination / Resource Area Memory v1 Live QA

Goal:
Run the resource-return, full-cycle, and cycle-history diagnostics against the
daily daemon. Confirm resource memory is populated near trees and that, after
bankingComplete=true with the bank closed and no visible tree, the system uses
a remembered resource return destination instead of failing missing
target.candidates immediately.

Expected behavior:
- If bankingComplete=true and bankOpen=true:
  - postBankReacquisitionNeeded=true
  - bankUiStillOpen=true
  - resourceTargetReacquisitionAllowed=false
  - reason=bank_ui_still_open
  - closeBankNeeded=true
  - activeIntent=close_service_context
  - missing target candidates should not be treated as a resource targeting failure.
- If bankingComplete=true and bankOpen=false:
  - worldViewReady=true
  - resourceTargetReacquisitionAllowed=true
  - resource target selection resumes normally.
- If bank closed and resource target visible:
  - phase should progress toward target_selected / select_target.
- If bank closed and no resource target visible:
  - if valid resource memory exists, phase should be return_to_resource and
    activeIntent should be return_to_resource_area
  - if no valid resource memory exists, phase should be needs_more_context with
    reason no_resource_memory or no_resource_target_observed.

Live retest commands:
```powershell
python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\diagnose_bank_operation_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_return_to_resource_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_post_bank_reacquisition_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_close_bank_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
