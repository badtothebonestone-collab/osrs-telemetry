# Context API

The context API is a read-only local sidecar around existing live telemetry
files and daemon/plugin-derived context. Responses are compact by default and
include only requested sections.

## Endpoints

- `GET /health`
- `GET /status`
- `GET /schema`
- `GET /capabilities`
- `GET /recordings`
- `GET /recordings/<id>/summary`
- `GET /recordings/<id>/schema-gap`
- `POST /context`
- `POST /context/batch`

## Request Shape

```json
{
  "schema": "context_request.v1",
  "requestId": "example",
  "needs": ["baseline", "inventory"],
  "responseMode": "compact",
  "maxCandidates": 3,
  "maxAgeTicks": 5,
  "maxAgeMillis": 5000
}
```

Useful needs include `baseline`, `liveness`, `inventory`, `equipment`, `bank`,
`bank_ui`,
`widgets`, `hover`, `menu`, `nearby_objects`, `nearby_npcs`, `diagnostics`,
`capabilities`, `best:object:<name_or_class>`, `nearest:object:<name_or_class>`,
`best:npc:<name_or_class>`, `nearest:npc:<name_or_class>`,
`route_objects`, `best:route:<name_or_action>`, and
`nearest:route:<name_or_action>`. For woodcutting task interpretation, request
`woodcutting_lifecycle` or `lifecycle:woodcutting`. For banking interpretation,
request `banking_lifecycle`, `banking`, `bank_state`, `bank_ui`, or
`inventory_delta`.

For combat interruptions, request `combat_state`, `interruption_lifecycle`,
`combat_damage_summary`, `damage_taken`, `damage_dealt`, or
`primary_opponent`.

For advisory click planning, request `click_plan`, `human_click_plan`, or
`click_planning_context`. The response uses current target evidence plus
`human_click_profile.json`; it does not execute input and does not invent
coordinates when geometry is missing.

## Response Shape

```json
{
  "schema": "context_response.v1",
  "requestId": "example",
  "generatedAtUtc": "2026-06-02T00:00:00Z",
  "latestTick": 123,
  "latestExportSequence": 456,
  "status": "PASS",
  "freshness": {},
  "warnings": [],
  "missingCapabilities": [],
  "sourceFilesSummary": {}
}
```

`status` is `PASS`, `WARN`, or `FAIL`. Missing optional telemetry should become
warnings and `missingCapabilities`, not invented field values.

## Capabilities Shape

`GET /capabilities` returns the existing capability registry plus
`telemetryDiscovery`, which reports field presence, missing fields, stale
sources, parse warnings, latest tick/export sequence, and schema gap categories.

## Examples

Baseline plus inventory:

```json
{"schema":"context_request.v1","needs":["baseline","inventory"],"responseMode":"compact"}
```

Hover plus menu:

```json
{"schema":"context_request.v1","needs":["hover","menu"],"maxCandidates":5}
```

Nearest object:

```json
{"schema":"context_request.v1","needs":["nearest:object:tree"],"maxCandidates":3}
```

Best object:

```json
{"schema":"context_request.v1","needs":["best:object:bank"],"maxCandidates":3}
```

Nearest route object:

```json
{"schema":"context_request.v1","needs":["route_objects","nearest:route:staircase"],"maxCandidates":3}
```

Combat damage:

```json
{"schema":"context_request.v1","needs":["combat_damage_summary","damage_taken","damage_dealt","primary_opponent"],"responseMode":"compact"}
```

If hitsplat amounts are unavailable, damage totals are `null` and warnings name
the missing capability. If amounts are available, the compact response includes
damage taken/dealt, hitsplat count, HP change, primary opponent, actor death,
and task-resume evidence.

Human click planning:

```json
{"schema":"context_request.v1","needs":["click_plan"],"task":"woodcutting","activity":"woodcutting","action":"Chop down","responseMode":"compact"}
```

The compact `clickPlan` includes status, task, action, target, center point,
profile-informed point, offset, confidence, reasons, warnings, and blockers.

Bank diagnostics:

```json
{"schema":"context_request.v1","needs":["bank","widgets","inventory","diagnostics","capabilities"],"responseMode":"compact"}
```

Banking lifecycle:

```json
{"schema":"context_request.v1","needs":["banking_lifecycle","bank_state","bank_ui","inventory_delta"],"responseMode":"compact"}
```

Woodcutting lifecycle:

```json
{"schema":"context_request.v1","task":"woodcutting","needs":["baseline","inventory","activity","best:tree","woodcutting_lifecycle"],"maxCandidates":1,"responseMode":"compact"}
```

Recording summary:

```powershell
curl http://127.0.0.1:8890/recordings/20260602_190000_chop_tree/summary
```

Recording summaries include compact menu interaction fields when analysis has
run:

- `menuInteractionCounts`
- `menuSelections`
- `menuSelectionsWithRowGeometry`
- `menuSelectionsLinkedToTargets`

Recording summaries include compact banking fields when
`banking_lifecycle.json` or `summary.json.banking_lifecycle` is present:

- `bankingLifecycleStatus`
- `bankOpenSeen`
- `depositBoxOpenSeen`
- `bankContainerAvailable`
- `bankUiPresent`
- `bankUiSnapshotCount`
- `bankUiFreshness`
- `depositedItemCount`
- `depositedItems`
- `bankingMissingCapabilities`

Recording summaries also include compact traversal fields when
`traversal_lifecycle.json` or `summary.json.traversal_lifecycle` is present:

- `traversalStatus`
- `routeName`
- `traversalStepCount`
- `routeSegmentCount`
- `successfulSegmentCount`
- `partialSegmentCount`
- `reviewEvidenceCount`
- `startArea`
- `endArea`
- `traversalSummary`

When a route template comparison is available, recording summaries include:

- `routeTemplatePath`
- `routeTemplateComparisonPath`
- `detectedRouteName`
- `detectedStartArea`
- `detectedEndArea`
- `routeTemplateAutoSelection`
- `routeTemplateDirectionMismatch`
- `untemplatedRoute`
- `suggestedTemplateName`
- `routeTemplateStatus`
- `routeTemplateStatusReason`
- `routeTemplateScore`
- `routeTemplateRevision`
- `matchedVariantName`
- `validUnregisteredVariant`
- `navigationSupportSubstitutions`
- `navigationSupportEvidenceCount`
- `allowedExtraSegmentCount`
- `reviewEvidenceSegmentCount`
- `matchedSegmentCount`
- `requiredSegmentCount`
- `missingSegmentCount`
- `extraSegmentCount`
- `weakSegmentCount`
- `routeTemplateComparison`

Context requests can ask for the latest recording route summary:

```json
{"schema":"context_request.v1","needs":["latest_recording_traversal"],"responseMode":"compact"}
```

Context requests can also ask for route-template data:

```json
{"schema":"context_request.v1","needs":["route_template","route_template_comparison","latest_route_comparison"],"responseMode":"compact"}
```

The compact response omits raw traversal steps by default and returns only the
template route name, segment counts, comparison status, status reason, matched
variant name, navigation-support substitution count, score, missing/extra
counts, and warnings.

Common route-template status reasons are `PASS_BASE_TEMPLATE`,
`PASS_REGISTERED_VARIANT`, `WARN_VALID_UNREGISTERED_VARIANT`, and
`FAIL_WRONG_ENDPOINT`. Consumers should treat review evidence and harmless
navigation-support clicks as debugging context unless the route endpoint,
required ordering, or postconditions fail.

Route templates are directional. A reverse route should use its own template.
When Record Everything analysis uses auto-selection, context summaries expose
the detected route/start/end and the selected template. If a strict comparison
uses the wrong one-way template, `routeTemplateDirectionMismatch` is true.

Route monitor requests evaluate live readiness/progress against a supplied
template:

```json
{"schema":"context_request.v1","needs":["baseline","route_monitor"],"routeTemplate":"route_templates/Bank_to_Woodcutting_area.route_template.json","responseMode":"compact"}
```

Use `"routeTemplate":"auto"` when the caller wants the monitor to choose a
template from the current live area. Auto mode does not guess when telemetry is
stale or the current area is unknown.

Related needs are `route_readiness`, `route_progress`, and
`route_next_segment`. Compact responses include `routeMonitor`,
`routeReadiness`, `routeProgress`, and `routeNextSegment` with route state,
current area, completed/remaining segment counts, off-route status, freshness,
and warnings. Recording summaries include `routeMonitorStatus`, `routeState`,
`nextExpectedSegment`, and `offRoute` when `route_monitor_status.json` is
present.

Persistent route history can be requested with:

```json
{"schema":"context_request.v1","needs":["route_history"],"responseMode":"compact"}
```

If a route history session is running or has written
`route_session_state.json`, the response includes route state, current area,
current/next segment, completed/remaining segments, off-route status,
freshness, and warnings. Use `routeSessionStatePath` to point at a specific
state file. Related needs are `route_session_state`,
`route_progress_timeline`, `route_completed_segments`, and
`route_remaining_segments`.

## Warning Behavior

If a requested section is unavailable, the response keeps the section compact,
sets `status` to at least `WARN`, and adds a stable missing capability name. For
target helpers, candidates include `missingFields` such as `ref`,
`effectiveName`, `effectiveActions`, `worldPoint`, `distance`, or `aimGeometry`.
Route helpers use already-exported route object census data when present and
return compact route candidates with identity, actions, world point, distance,
route kind, confidence, reasons, and missing fields.

`woodcuttingLifecycle.phase` is the compact lifecycle interpretation. Common
phases are `tree_available`, `chop_clicked`, `chopping`, `log_gained`,
`target_depleted`, and `inventory_full`. Missing lifecycle signals produce a
`WARN` or `FAIL` lifecycle status without inventing clicks, logs, or targets.

`bankingLifecycle.phase` is the compact bank/deposit interpretation. Direct
bank-open/container state produces stronger evidence. Inventory/menu-only
deposit proof is useful but reports `WARN` plus missing bank capabilities.

Banking callers can request compact direct-state and deposit summaries:

```json
{"schema":"context_request.v1","needs":["banking","bank_state","inventory_delta","deposit_result"],"responseMode":"compact"}
```

The compact banking response includes lifecycle status, phase, confidence,
`bankOpen`, `depositBoxOpen`, `activeBankLikeInterface`,
`bankContainerAvailable`, `bankContainerDeltaAvailable`, inventory free slots,
deposited/withdrawn item summaries, `depositConfirmationLevel`,
`missingCapabilities`, and warnings. Raw bank item arrays are omitted by default;
scripts that need the result of a deposit should use `deposit_result`.

## Versioning

Use schema fields as compatibility boundaries:

- Request schema: `context_request.v1`
- Context response: `context_response.v1`
- Capabilities: `capability_registry.v1` plus `telemetry_capabilities.v1`
- Normalized telemetry: `normalized_telemetry.v1`
- Manual recording analysis: `manual_telemetry_analysis.v1`

Additive fields are safe. Breaking changes should introduce a new schema
version rather than changing existing field meanings.
## Combat And Interruption Context

Compact context needs:
- `combat_state`
- `combat`
- `recent_hitsplats`
- `recent_stat_changes`
- `recent_chat_messages`
- `interruption_lifecycle`
- `current_interruption`
- `task_interruption_status`

Example:

```json
{
  "schema": "context_request.v1",
  "needs": ["combat_state", "interruption_lifecycle"],
  "responseMode": "compact"
}
```

Use these fields to answer whether combat was observed, whether an NPC targeted the player, whether hitsplats/stat/chat evidence appeared, and whether a task interruption was inferred or directly explained.

## Woodcutting Loop Context

Compact context needs:

- `woodcutting_loop`
- `woodcutting_loop_lifecycle`
- `task_loop`
- `next_expected_phase`

Example:

```json
{
  "schema": "context_request.v1",
  "needs": ["woodcutting_loop", "next_expected_phase"],
  "responseMode": "compact"
}
```

The compact response includes `woodcuttingLoop` with `loopState`,
`currentPhase`, `nextExpectedPhase`, confidence, warnings, and missing
capabilities. Scripts should consume these compact fields instead of stitching
together raw lifecycle artifacts.
