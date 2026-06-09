# Human Click Planning

`human_click_plan.v1` is a bounded dry-run planning layer. It uses the aggregate
human click/camera profile plus current target and readiness evidence to explain
where a click would land.

It does not execute input.

## Inputs

- Live or synthetic target evidence: target name, action, visibility, aim point,
  geometry, and target quality.
- Readiness/hover/menu evidence from action visibility or context.
- `human_click_profile.json`, especially median/p75 aim distance, menu-row
  behavior, and camera-before-click frequency.
- Task state from `task_script_api`: banking, deposit result, woodcutting loop,
  route monitor, and interruption state.

## Gates

The planner keeps existing safety rules intact:

- Missing target or aim geometry returns `WARN`; it does not invent coordinates.
- Weak target quality without hover/menu proof returns `WARN`.
- Off-route route monitor state returns `FAIL` for normal click planning.
- Inventory-full woodcutting returns a route-to-bank plan instead of a Chop down
  click.
- Deposit-complete banking returns a route-to-trees plan instead of another
  deposit click.
- If the profile says camera-before-click is common and target visibility is
  weak, the plan recommends `camera_adjust_first`.

## Human Profile vs Center Click

The base point remains the live target aim point or menu-row point. The
profile-informed point applies a small deterministic offset derived from the
human profile median/p75 aim distances and then clamps it to known bounds when
available. This is meant to compare human-like aim with a robotic center click,
not to add randomness or bypass readiness checks.

## CLI Dry Run

```powershell
python telemetry-viewer\execute_next_action.py --dry-run-click-plan --json
```

This prints `execute_next_action_click_plan.v1` and does not initialize a live
input backend.

## Context API

```json
{
  "schema": "context_request.v1",
  "needs": ["click_plan"],
  "task": "woodcutting",
  "activity": "woodcutting",
  "action": "Chop down",
  "responseMode": "compact"
}
```

Compact response field:

```json
{
  "clickPlan": {
    "status": "PASS|WARN|FAIL",
    "task": "woodcutting",
    "action": "Chop down",
    "target": "Tree",
    "plannedPoint": {"x": 104, "y": 117},
    "centerPoint": {"x": 100, "y": 120},
    "offset": {"dx": 4, "dy": -3},
    "confidence": 0.7,
    "reasons": [],
    "warnings": [],
    "blockedReasons": []
  }
}
```

## Script API

```python
import task_script_api as api

plan = api.get_human_click_plan(
    target={"name": "Tree", "targetQuality": "strong", "onScreen": True, "geometryAvailable": True, "aimPoint": {"x": 100, "y": 120}},
    action="Chop down",
    activity="woodcutting",
)
```

Use `get_next_click_plan(source)` when a script already has compact runtime
state and target/readiness evidence.

## Current Limitation

The planner is advisory. Live click generation does not yet consume the
profile-informed point. The next validation step should compare planned points
against future Record Everything recordings before enabling any live click
placement change.

The `20260607_190145_Cutting_a_tree_or_two_with_camera_movement` fixture now
recovers a Tree aim point for `Chop down / Tree` (`{x: 489, y: 234}`) and rejects
the unrelated Gate geometry. That makes aim-point comparison possible. It still
lacks selected-Tree clickbox or tile-polygon geometry, so clickbox containment
validation remains an open gap.
