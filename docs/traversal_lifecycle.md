# Traversal Lifecycle

Traversal lifecycle turns a manual recording into a compact route story:
where the player started, where they ended, which traversal actions happened,
and which postconditions proved or weakened each step.

The output artifact is:

```text
traversal_lifecycle.json
```

Its schema is `traversal_lifecycle.v1`.

## Why It Exists

Route recordings naturally contain more than one action. A bank-to-woodcutting
or woodcutting-to-bank run can include walking, doors, ladders, staircases,
camera movement, and menu selections. The traversal lifecycle layer does not
require exactly one menu selection. It summarizes the whole sequence and lets
menu row geometry, target quality, coordinate alignment, and player movement
each provide separate evidence.

## Step Extraction

The analyzer reads existing artifacts:

- `events.jsonl`
- `joined_input_telemetry.jsonl`
- `input_action_classifications.jsonl`
- `target_match_quality.jsonl`
- `menu_interactions.jsonl`
- `coordinate_alignment_summary.json`
- `camera_behavior_summary.json`
- `vm_mouse_arduino_mapping.json`
- `summary.json`

It extracts:

- `walk`: player world position changed on the same plane.
- `object_action`: target-relative object actions such as Door/Open,
  Ladder/Climb-up, Staircase/Climb-down, and Trapdoor/Climb.
- `menu_selection`: a menu row selection linked to a game target/action.
- `plane_transition`: player plane changed, with nearby route-object evidence
  when available.
- `area_transition`: inferred by start/end area labels.
- `bank_interaction`: bank booth or deposit box interactions.
- `task_interaction`: non-traversal task work, such as tree chopping.

Each step stores target quality, input classification, row geometry status,
coordinate transform, pre/post player position, pre/post plane, result,
confidence, evidence, and warnings.

## Route Step Grouping

Raw evidence can be noisy. A single successful route can produce a target click,
movement snapshots, menu rows, and a plane transition that all describe the same
moment. The lifecycle therefore keeps several views:

- `rawSteps`: direct extracted evidence.
- `groupedSteps` / `steps`: cleaned route-progress steps.
- `routeSegments`: the compact route story to read first.
- `reviewEvidence`: useful low-confidence, duplicate, non-progress, or
  context-only evidence that should not weaken the route verdict by itself.

Grouping attaches related postconditions to the route action they prove. For
example, a `Climb-up` click followed by a plane change becomes one climb
segment, with the plane transition attached as supporting evidence. A
Door/Open action followed by movement becomes one door transition segment.
Weak or unrelated clicks move to review evidence.

The grouping block reports:

- `rawStepCount`
- `groupedStepCount`
- `routeSegmentCount`
- `supportingEvidenceCount`
- `reviewEvidenceCount`
- `grouping.partialStepsResolved`

## Postconditions

The lifecycle looks forward after each action using a short time/tick window.

- Ladder, staircase, stairs, and trapdoor actions are strong when a plane
  changes after the click.
- Door/Open is successful when the player position or plane changes after the
  action.
- Bank actions are successful when bank/widget state opens; otherwise a strong
  target match becomes a medium warning.
- Walk steps are successful when the player position changes.
- Tree/Chop is retained as task interaction and can be interpreted by the
  woodcutting lifecycle.

Route-specific windows are intentionally short but not identical:

- climb/plane transition: about 5 seconds or 8 ticks
- door/open movement: about 4 seconds or 6 ticks
- walk movement: about 5 seconds or 8 ticks
- area arrival: summarized from the end of the recording

Warnings are attached to the specific step and also rolled up to the lifecycle
summary.

## Area And Route Labels

Area labels are intentionally simple:

- `bank_area`: nearby bank booth, deposit box, or bank evidence.
- `woodcutting_area`: nearby tree evidence.
- `plane_N`: fallback when only plane evidence is clear.

Known fixture routes are inferred when evidence supports them:

- `Bank_to_Woodcutting_area`
- `woodcutting_area_to_bank`

These route names are directional. A traversal that starts in
`woodcutting_area` and ends in `bank_area` should use the
`woodcutting_area_to_bank` route template, not the forward bank-to-woodcutting
template.

## How Target Quality Is Used

Strong and medium target matches improve step confidence. Weak or unmatched
clicks remain visible as warnings. Missing menu row geometry does not fail a
route if target quality and movement or plane-change postconditions are strong.

## How To Record A Route

Use the `Route / Traversal Recording` UI preset, or include the analyzer flag:

```powershell
--traversal-lifecycle
```

For route recordings that include menus, keep menu burst capture enabled:

```powershell
--menu-capture-burst --menu-burst-until-selection --menu-burst-tail-ms 500 --menu-burst-max-ms 4000
```

For Arduino evidence during manual validation, use `map_only` click policy so
the recorder keeps Arduino mapping artifacts without sending duplicate live
clicks.

## How To Analyze A Route

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --input-trace --join-input --camera-behavior --vm-mouse-mapping --classify-input-actions --target-match-quality --menu-interactions --coordinate-alignment --input-path-integrity --arduino-mirror-verification --menu-row-diagnostics --traversal-lifecycle --group-traversal-steps --print-route-segments
```

Review:

- `traversal_lifecycle.json`
- `summary.json` under `traversal_lifecycle`
- `schema_gap_report.md` under `Traversal Lifecycle`

Start with `routeSegments`. If a route is WARN, inspect `reviewEvidence` and
`rawSteps` to see which clicks or movement rows were folded out of the main
story.

## Route Templates

Once a route summary is clean enough to reuse, extract a route template from
`routeSegments`:

```powershell
python telemetry-viewer\analyze_manual_recording.py "<recording_folder>" --summary --schema-gap --traversal-lifecycle --group-traversal-steps --extract-route-template --route-template-out route_templates
```

Route templates deliberately use `routeSegments` instead of `rawSteps`. The
template comparison layer checks required route progress, start/end areas,
postconditions, order, and target quality, while leaving review evidence as
debug context. See `docs\route_templates.md` for the extraction and comparison
workflow.

Record Everything analysis uses route-template auto-selection when traversal
signals are present. The analyzer matches by detected route name first, then by
start/end areas. If no matching one-way template exists, it reports an
untemplated route and suggests extracting one from the recording.

The route monitor uses the same routeSegments/template comparison semantics for
readiness and progress. Use it when you want to know the current route state
from live telemetry or summarize whether a recording is complete, stale,
blocked, or off-route. See `docs\route_monitor.md`.

Template comparison can also register route variants. A variant does not
rewrite the base route. It explains a known substitution only when the base
route semantics are already correct.

If a successful route recording contains a segment that is not actually
required, fix the template semantics instead of registering a variant around
the mistake. For `Bank_to_Woodcutting_area`, Door/Open is optional navigation
evidence. `Open Door` and `Walk here Large door` can be useful support or
review evidence, but missing Door/Open does not weaken the route when start,
stairs/plane transition, walking, and end area all match.

Minimap or navigation-support clicks are not required route segments by
default. They should become supporting evidence for a walk segment, or review
evidence when harmless. They matter only when they change the route outcome,
miss a required transition, reorder strict route progress, or fail a
postcondition.

## Examples

Door/Open:
The click can be a menu-row selection. The row geometry proves the menu action,
while the linked target/action and later position or plane change prove the
route step. Treat Door/Open as required only for routes whose semantics require
opening a door.

Ladder or Staircase:
`Climb-up` or `Climb-down` followed by a player plane change is a strong
traversal success signal.

Bank to Woodcutting:
Starts in `bank_area`, traverses down route objects, walks to trees, and ends
in `woodcutting_area`.

Woodcutting to Bank:
Starts near trees, traverses route objects upward, walks into bank context, and
ends in `bank_area`.

## Loop Lifecycle

`woodcutting_loop_lifecycle.json` consumes traversal output to classify route
phases:

- `woodcutting_area -> bank_area` becomes `routing_to_bank`, next
  `banking_deposit`.
- `bank_area -> woodcutting_area` becomes `routing_to_trees`, next
  `resume_cutting`.

Route proof still comes from `routeSegments`, template comparison, and route
monitor/history artifacts. The loop layer only names the task phase.
