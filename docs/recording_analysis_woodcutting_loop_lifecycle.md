# Woodcutting Loop Lifecycle Analysis

Generated: `2026-06-07`

## Overview

`woodcutting_loop_lifecycle.v1` now combines existing lifecycle artifacts into a
task-level loop state. It does not add raw telemetry or change combat
attribution. It reads woodcutting, banking, traversal/route, route monitor,
interruption, combat damage, human click profile, and summary artifacts when
present.

## Phase Rules Implemented

- `at_trees`: woodcutting area evidence or woodcutting lifecycle signal.
- `cutting`: animation `879`, fresh `Chop down`, logs gained, or active
  woodcutting phase.
- `inventory_full`: `freeSlotsEnd == 0` or lifecycle inventory full.
- `inventory_near_full`: `freeSlotsEnd <= 1`.
- `routing_to_bank`: route/traversal from `woodcutting_area` to `bank_area`.
- `banking`: direct bank/deposit lifecycle evidence.
- `deposit_complete`: deposited items, especially logs.
- `routing_to_trees`: route/traversal from `bank_area` to `woodcutting_area`.
- `interrupted`: interruption lifecycle detected.
- `resumed_cutting`: interruption lifecycle says `taskResumed=true`.

## Existing Recording Results

| Recording | Status | Loop state | Current phase | Next expected phase | Confidence |
| --- | --- | --- | --- | --- | --- |
| `20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory` | PASS | `inventory_full` | `inventory_full` | `route_to_bank` | 0.95 |
| `20260607_104613_Woodcutting_area_to_bank` | PASS | `routing_to_bank` | `routing_to_bank` | `banking_deposit` | 0.9 |
| `20260607_120446_Bank_opening_deposit` | PASS | `deposit_complete` | `deposit_complete` | `route_to_woodcutting_area` | 0.95 |
| `20260606_201613_Bank_to_tree_area` | PASS | `routing_to_trees` | `routing_to_trees` | `resume_cutting` | 0.9 |
| `20260607_154606_Wood_cutting_attacked` | PASS | `resumed_cutting` | `resumed_cutting` | `continue_current_phase` | 0.95 |
| `20260607_171427_Wood_cutting_attacked` | PASS | `complete` | `complete` | `continue_current_phase` | 0.95 |

## Script-Facing Behavior

New helper functions in `task_script_api.py`:

- `get_woodcutting_loop_lifecycle(source)`
- `get_current_task_phase(source)`
- `get_next_expected_phase(source)`
- `is_inventory_full_for_woodcutting(source)`
- `did_deposit_logs(source)`
- `should_route_to_bank(source)`
- `should_route_to_trees(source)`
- `was_interrupted(source)`
- `did_resume_after_interruption(source)`

Scripts can now ask for task phase and next phase without parsing raw lifecycle
JSON files.

## Context Behavior

New compact context needs:

- `woodcutting_loop`
- `woodcutting_loop_lifecycle`
- `task_loop`
- `next_expected_phase`

Compact responses include `woodcuttingLoop`, `taskLoop`, and
`nextExpectedPhase` where requested.

## UI Behavior

Simple Mode remains unchanged. Analyze Latest now requests
`--woodcutting-loop-lifecycle`, and the compact analysis line can show loop
state plus next expected phase when available.

## Full Loop Fixture

`20260607_171427_Wood_cutting_attacked` is the current full-loop fixture. The
label is stale, but the data proves cutting, inventory full during the loop,
route to bank, bank-container deposit of `Logs x28`, route back to the
woodcutting area, combat interruption, and resumed cutting.

The remaining useful follow-up is not another proof of concept; it is a clean
uninterrupted full-loop sample if timing or human-click profile tuning needs a
less noisy fixture later.
