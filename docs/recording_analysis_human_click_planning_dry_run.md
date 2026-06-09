# Human Click Planning Dry Run

Date: 2026-06-07

This report validates the first bounded human-profile-informed click planner. The planner is advisory only: it does not send input, and every output still has to pass live readiness, hover/menu proof, target geometry, and executor safety before a click can happen.

## Inputs

- Human click profile: `telemetry-viewer/knowledge_base/human_click_profile.json` style fields.
- Script state: banking, woodcutting loop, route monitor, and target/readiness summaries.
- Planner module: `telemetry-viewer/input_control/click_planner.py`.
- Output schema: `human_click_plan.v1`.

## Sample Plans

### Woodcutting Tree / Chop down

- Status: PASS
- Action: Chop down
- Target: Tree
- Target quality: strong
- Center point: `{x: 640, y: 360}`
- Profile point: `{x: 643, y: 358}`
- Offset: `{dx: 3, dy: -2}`
- Confidence: 0.70
- Reasons: profile-informed non-center point, strong target quality, geometry available, target visible
- Blockers: none

This is the intended difference from a robotic center click: the plan remains inside the target bounds but nudges the aim point using the human profile.

### Inventory Full Woodcutting State

- Status: WARN
- Requested action: Chop down
- Planned action: route_to_bank
- Target: Tree
- Center point: `{x: 640, y: 360}`
- Profile point: `{x: 643, y: 358}`
- Blocker: inventory_full_route_to_bank_required

The planner does not recommend another chop when the loop state says inventory is full. It keeps the center-vs-profile comparison for audit, but the task decision changes to route toward the bank.

### Banking Deposit Already Complete

- Status: WARN
- Requested action: Deposit
- Planned action: route_to_woodcutting_area
- Target: Deposit inventory
- Center point: `{x: 702, y: 466}`
- Profile point: `{x: 699, y: 468}`
- Blocker: deposit_complete_route_to_trees_required

The planner does not recommend another deposit click after deposit completion. The next expected phase is returning to the woodcutting area.

### Route Monitor Off Route

- Status: FAIL
- Action: Climb-up
- Target: Staircase
- Center point: `{x: 518, y: 299}`
- Profile point: `{x: 521, y: 297}`
- Blocker: route_monitor_off_route

When route monitor reports off-route, normal route click planning is blocked even if a target point exists.

## Decision Gates

- Strong or medium target quality is preferred.
- Weak target evidence without hover/menu confirmation returns WARN.
- Missing geometry returns WARN and does not fabricate coordinates.
- Human profile offsets are bounded and clamped to available geometry.
- Menu-row geometry is used when the intended action is a menu row.
- Inventory-full woodcutting redirects to route_to_bank.
- Completed banking deposit redirects to route_to_woodcutting_area.
- Off-route state blocks normal route planning.
- Camera-before-click profile can recommend `camera_adjust_first` when target visibility or geometry is weak.

## Live Dry Run

`execute_next_action.py --dry-run-click-plan --json` produced a safe WARN on the current live context because no trustworthy live target/geometry was available. That is the correct behavior for this pass: the planner can describe blockers without sending a click.

## Remaining Validation

- Compare dry-run planned aim points against future successful Record Everything clicks.
- Keep live click generation unchanged until replay validation shows profile offsets improve or preserve success.
- Add route-specific and menu-row-heavy fixtures to check whether task buckets need different offset magnitudes.
