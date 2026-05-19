# Current Codex Handoff

Repo:
`C:\Users\stone\osrs-telemetry\example-plugin`

New Codex chats should read `AGENTS.md` first, then this file. Do not treat
older chat history as source of truth. Use the current repo, current tests,
current diagnostics, `AGENTS.md`, and this handoff.

## Current Daily Path

Snapshot No-File is the daily path.

Daily daemon command:

```powershell
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

Architecture:

```text
RuneLite plugin
-> plugin snapshot endpoint
-> live_core_daemon.py
-> analyzers
-> Mission Control / diagnostics / overlay
```

## Current Completed Baseline

- Snapshot No-File is the daily path.
- No continuous runtime JSON/NDJSON output should be added.
- Scanner/checker/filter field-name systems were removed and should not return.
- Do not strip useful telemetry fields because of names like `actions`,
  `menuActions`, `clickbox`, `target`, `path`, `interaction`, `destination`,
  or `waypoint`.
- Filtering should only happen for explicit performance, size, display, or
  task-selection reasons.
- Service selection works.
- Bank booth wins over Deposit Box when visible.
- Retained Bank booth blocks Deposit Box fallback.
- Collision window cache works.
- OSRS-like path prediction works.
- Tile overlay works.
- Path intent stabilization works.
- Arrival/serviceReady works.
- Bank UI / Service State Context v1 works.
- Bank Operation Context v1 works.
- Return-to-Resource Context v1 works.
- Post-bank World Reacquisition Context v1 works.
- Close-bank Readiness / Return Control Context v1 works.
- Cycle History / State Transition Trace v1 works.
- Resource Return Destination / Resource Area Memory v1 works and has live QA.
- Full Woodcut Bank Cycle QA Harness v1 works.
- Full Cycle Synthetic Scenario Suite v1 works.
- Full Cycle Live QA Runner v1 works.
- Mission Control Cycle Summary v1 works and has live panel QA.
- Unified Input Control Package v1 works.
- Dynamic RuneLite Canvas Geometry v1 works.
- Action Lifecycle Cooldown / Single-Action Loop v1 works.
- RuneLite Dev Bootstrap / Login Flow Helper v2 is implemented. Current live
  run confirms launch, secondary-monitor placement, bounded startup clicks,
  `LOGGED_IN` detection, daemon start/reuse, and live QA handoff. The bootstrap
  waits after `Play Now` for the server transition before clicking the final
  `CLICK HERE TO PLAY` panel.
- Stabilization suite currently passes 130/130.

## Woodcut Bank Cycle Summary

The woodcut_bank loop is modeled from resource collection through service,
banking, close-bank/world reacquisition, and return-to-resource:

- Collect resources until inventory state requires service.
- Select full-bank service targets with Bank booth / banker / bank chest as
  primary targets and Deposit Box / deposit chest as fallback targets.
- Preserve retained Bank booth context so a temporary missing booth candidate
  does not incorrectly fall back to Deposit Box.
- Use pathing and arrival/serviceReady context to distinguish walking to bank
  from being ready to interact with the service.
- Observe bank UI state after serviceReady:
  bankOpen, bankReadable, bankPinOpen, widget visibility, close capability, and
  compact inventory/bank summaries.
- Evaluate bank operation state:
  resources held, resource slots/quantity, non-resource item count, deposit
  availability, operationNeeded, operationType, and bankingComplete.
- When bankingComplete=true and bankOpen=true, defer target candidates as
  bank UI still open / close bank needed rather than reporting a resource
  targeting failure.
- When bankingComplete=true and bankOpen=false, resume resource targeting.
- If no resource target is visible after banking and valid resource memory
  exists, use the remembered resource area as a return destination.
- If a resource target becomes visible, normal target selection wins over the
  remembered return destination.

## Current Diagnostics

One-command live QA:

```powershell
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
```

RuneLite dev bootstrap:

```powershell
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --dry-run
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --start-daemon --run-live-qa
python telemetry-viewer\run_runelite_bootstrap.py --skip-runelite-launch --execute --start-daemon --run-live-qa
python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --move-to-secondary-monitor --start-daemon --run-live-qa --print-candidates --timeout-seconds 180
```

The bootstrap helper is only for already-authenticated startup flow. It may
launch `.\gradlew.bat run`, focus a RuneLite/Jagex/Java window, optionally move
the client to the secondary monitor, click deterministic Play/Continue/CLICK
HERE TO PLAY candidates, wait for `LOGGED_IN`, start/reuse the daily daemon,
and run the live QA runner. It must not type passwords, change account settings,
store credentials, select worlds, or change worlds; if a real login/account
confirmation prompt appears, stop and let the user handle it.

Synthetic scenario suite:

```powershell
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --scenario bank_closed_return_memory
python telemetry-viewer\diagnose_woodcut_bank_scenarios.py --json
```

Full cycle and history:

```powershell
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
```

Focused live diagnostics:

```powershell
python telemetry-viewer\diagnose_service_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_pathing_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_task_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting_bank
python telemetry-viewer\diagnose_bank_ui_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_bank_operation_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_return_to_resource_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_post_bank_reacquisition_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_close_bank_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_resource_return_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_overlay_state.py --latest-session --intent
```

Daily gauntlet:

```powershell
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
```

Plugin snapshot endpoint check:

```powershell
$request = @{
  schema = "plugin_snapshot_request.v1"
  needs = @("baseline", "writer_health")
  maxAgeTicks = 5
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/snapshot" -Body $request -ContentType "application/json"
```

## Synthetic Scenario Suite

`diagnose_woodcut_bank_scenarios.py` validates fixed in-memory states without
RuneLite, a live daemon, sessions, compact packets, or rolling files.

Current scenario names:

- `collecting_resources`
- `inventory_full_needs_service`
- `pathing_to_service`
- `service_ready_bank_closed`
- `bank_open_resources_held`
- `bank_open_after_deposit`
- `bank_closed_return_memory`
- `bank_closed_tree_visible`
- `bank_closed_no_memory_no_target`
- `bank_pin_blocked`
- `retained_booth_blocks_deposit`
- `remembered_return_cross_plane`

## Mission Control

`live_control_panel.py` shows a compact Mission Control cycle summary sourced
from daemon `/health`, `/status`, and `/control` fields. It should display:

- Cycle: cycleStage, phase, activeIntent, stable-for ticks, last transition.
- Inventory: inventory full/free slots and progress.
- Service / Path: selected service target, serviceReady, pathing needed and
  completed.
- Bank: bankOpen, bankReadable, bankPinOpen, operationNeeded, operationType,
  bankingComplete, closeBankNeeded, closeBankReady.
- Return: post-bank reason, return-to-resource reason, resource-return reason,
  returnDestinationAvailable.
- Health: overlay/live QA/gauntlet state when available, warning count, missing
  capability count, and noActionEmitted.

Live panel QA confirmed the panel fields match current daemon cycle diagnostics.
Live QA and gauntlet fields may show `unknown` unless those checks were run from
within the panel/runtime status path.

## Live QA Workflow

Codex may run terminal commands needed for live QA.

Preferred live QA flow:

1. Launch RuneLite dev when needed:

   ```powershell
   .\gradlew.bat run
   ```

   Or use the bootstrap helper:

   ```powershell
   python telemetry-viewer\run_runelite_bootstrap.py --launch-runelite --execute --move-to-secondary-monitor --start-daemon --run-live-qa --print-candidates --timeout-seconds 180
   ```

2. If RuneLite requires login, account confirmation, or anything
   credential-related, stop and ask the user to handle it.
3. Once the user confirms the client is logged in, continue automatically.
4. Wait for plugin snapshot endpoint `127.0.0.1:8893`.
5. Confirm plugin snapshot reports `LOGGED_IN`.
6. Start or restart `live_core_daemon.py` with the daily daemon command.
7. Run the live QA runner and relevant diagnostics.
8. Inspect whether live values look correct.
9. If values are wrong, make focused fixes based on the diagnostic fields and
   rerun the focused tests/diagnostics.

If Computer Use can operate the RuneLite dev window, Codex may click simple
already-authenticated buttons such as Play / Log in / Continue. Codex must not
handle credentials or account settings. If Computer Use cannot access the
RuneLite window, ask the user to click/log in manually, then continue with
endpoint checks and diagnostics.

Preferred live retest commands:

```powershell
python telemetry-viewer\run_woodcut_bank_live_qa.py --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\diagnose_woodcut_bank_cycle.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\diagnose_cycle_history.py --from-daemon --daemon-url http://127.0.0.1:8890 --tail 20
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
```

## Verification Commands

Always run focused tests for files changed.

Always run:

```powershell
python telemetry-viewer\run_stabilization_suite.py
```

If Java/plugin code changed, also run:

```powershell
.\gradlew.bat test
.\gradlew.bat build
```

If Python daemon/analyzer/diagnostic files changed, run relevant `py_compile`
checks, for example:

```powershell
python -m py_compile telemetry-viewer\live_core_daemon.py
python -m py_compile telemetry-viewer\diagnose_woodcut_bank_cycle.py
```

## Current Next Milestone

No next implementation milestone is set in this handoff. Keep future changes
focused on the user's current task and preserve the completed baseline above.
