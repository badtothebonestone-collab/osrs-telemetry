# Route Monitor Template Path And UI Simplification

Date: 2026-06-06

## Verdict

PASS for the template-path fix and route-session guardrails.

The live monitor can now resolve `Bank_to_Woodcutting_area`,
`Bank_to_Woodcutting_area.route_template.json`,
`route_templates\Bank_to_Woodcutting_area.route_template.json`, and the
absolute template path to the same template:

```text
C:\Users\badto\osrs-telemetry\route_templates\Bank_to_Woodcutting_area.route_template.json
```

It loads:

- routeName: `Bank_to_Woodcutting_area`
- templateRevision: `2`
- requiredSegmentCount: `5`

The short live follow smoke still reported stale telemetry, but it correctly
loaded the template and wrote under:

```text
C:\Users\badto\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\route_20260606_191948
```

## Root Cause

The previous live monitor session did not load the route template. The failed
session recorded:

- `templatePath`: `Bank_to_Woodcutting_area.route_template.json`
- `routeName`: `null`
- `templateRevision`: `null`
- output folder: `%USERPROFILE%\.osrs-telemetry\route_monitor\route\...`
- completed segments: `0`
- off-route events: `408`

The route itself was plausible. The launch/configuration path was wrong.

The exact weak path was:

1. UI command construction could pass the route template field as a bare
   filename or route-like value.
2. `route_monitor.py` loaded the path literally and did not hard-fail when the
   template payload was empty.
3. Follow-mode session state could be created without route name, revision, or
   required segment model.
4. With no required segments loaded, the monitor repeatedly classified fresh
   route samples as off-route instead of route progress.

The current saved UI config did not contain route template fields during this
inspection, so the persistent bad value was not proven to live in the saved
settings file. The code path was still enough to reproduce the class of
failure: a basename-only template could reach monitor launch without a
validated route model.

## Template Resolver

Added one canonical route template resolver in `route_template.py`.

Resolver schema:

```text
route_template_resolution.v1
```

It reports:

- input
- resolvedPath
- exists
- routeName
- templateRevision
- requiredSegmentCount
- status
- warnings
- candidatesTried

Search order:

1. absolute path if valid
2. repo root plus supplied relative path
3. repo root plus `route_templates` plus supplied filename
4. `route_templates\<routeName>.route_template.json`
5. configured default template path when the caller intentionally supplies one

Explicit unresolved inputs fail. UI config migration can still intentionally
fall back to the recommended default.

## Route Monitor Guardrails

`route_monitor.py` now resolves `--template` through the canonical resolver.

Follow/live mode validates before starting:

- template file exists
- routeName is loaded
- templateRevision is loaded
- required segment count is greater than zero

If validation fails, the monitor returns a configuration failure before the
follow loop starts. When an output directory is supplied, it writes
`route_monitor_error.json`.

Valid session state now includes:

- original template input
- resolved absolute template path
- route name
- template revision
- required segment count
- template resolution result

The output folder uses the loaded route name, not generic `route`.

## UI Simplification

The UI is now organized around the route workflow:

1. Start Telemetry
2. Select Route
3. Check Readiness
4. Start Route Session
5. Stop Route Session
6. Analyze + Compare

Always-visible route controls:

- route dropdown
- selected template path
- template loaded/missing status
- template revision
- current route state
- current area
- next expected segment
- completed/remaining segments
- off-route status
- freshness
- concise log tail
- `Check Route Readiness`
- `Start Route Session`
- `Stop Route Session`
- latest report/artifact openers

Advanced controls now hold the noisy configuration:

- detailed recording flags
- Arduino / mirror controls
- route monitor internals
- template management
- raw command preview
- low-level artifact buttons

## Route Session One-Click Behavior

`Start Route Session` builds a route session plan:

- validates the route template
- uses the resolved absolute template path
- starts route monitor follow mode
- starts a Route / Traversal recording
- writes a route session manifest tying together the monitor, recording
  controls, template path, route name, revision, and start time

`Stop Route Session` stops recorder and monitor, then runs analysis with:

- traversal lifecycle
- grouped route steps
- route template comparison
- route monitor
- route history

## Safe Route Preset

The Route / Traversal preset defaults to:

- `Bank_to_Woodcutting_area`
- template revision `2`
- telemetry preflight
- active/latest session preference
- polling input capture
- coordinate alignment
- menu interactions
- target match quality
- traversal lifecycle
- grouped traversal steps
- route monitor
- route history
- template comparison

Arduino is optional for route monitoring. Live Arduino mirror and live clicks
are disabled by default in the route preset.

## Config Migration And Reset

UI config migration resolves basename-only template paths through
`route_templates`. If old saved values are missing or bad, the route config is
reset to:

```text
route_templates\Bank_to_Woodcutting_area.route_template.json
```

Reset command:

```powershell
python telemetry-viewer\telemetry_ui.py --reset-config
```

Check mode now verifies:

- default template resolves
- routeName is `Bank_to_Woodcutting_area`
- templateRevision is `2`
- requiredSegmentCount is `5`
- route session plan can start
- Start Route Session monitor command uses the resolved absolute template path

## Validation Results

Template validation:

```powershell
python telemetry-viewer\route_monitor.py --validate-template Bank_to_Woodcutting_area
```

Result: PASS, resolved to the real template, routeName
`Bank_to_Woodcutting_area`, templateRevision `2`, requiredSegmentCount `5`.

```powershell
python telemetry-viewer\route_monitor.py --validate-template Bank_to_Woodcutting_area.route_template.json
```

Result: PASS, resolved to the real template, routeName
`Bank_to_Woodcutting_area`, templateRevision `2`, requiredSegmentCount `5`.

```powershell
python telemetry-viewer\route_monitor.py --validate-template route_templates\Bank_to_Woodcutting_area.route_template.json
```

Result: PASS, resolved to the real template, routeName
`Bank_to_Woodcutting_area`, templateRevision `2`, requiredSegmentCount `5`.

Short live follow smoke:

```powershell
python telemetry-viewer\route_monitor.py --template Bank_to_Woodcutting_area --latest-session --live --follow --duration 1 --poll-ms 250 --json
```

Result: WARN stale, as expected from current live source age, but template
loading is correct:

- routeName: `Bank_to_Woodcutting_area`
- templateRevision: `2`
- requiredSegmentCount: `5`
- outputDir:
  `C:\Users\badto\.osrs-telemetry\route_monitor\Bank_to_Woodcutting_area\route_20260606_191948`
- routeState: `stale`
- offRoute: `false`
- warning: `telemetry stale`

## Checks Run

```powershell
python -m py_compile telemetry-viewer\route_template.py
python -m py_compile telemetry-viewer\route_monitor.py
python -m py_compile telemetry-viewer\telemetry_ui.py
python -m py_compile telemetry-viewer\analyze_manual_recording.py
python -m py_compile telemetry-viewer\context_service.py
python telemetry-viewer\tests\test_route_template.py
python telemetry-viewer\tests\test_route_monitor.py
python telemetry-viewer\tests\test_telemetry_ui.py
python telemetry-viewer\telemetry_ui.py --check
```

All passed.

## Next Foolproof Route Session

UI steps:

1. Open `OSRS Telemetry Control`.
2. Click `Start Telemetry Stack`.
3. Select `Bank_to_Woodcutting_area`.
4. Confirm template status says loaded, revision `2`.
5. Click `Check Route Readiness`.
6. If readiness is fresh and not off-route, click `Start Route Session`.
7. Perform the route.
8. Click `Stop Route Session`.
9. Review the compact verdict and open the latest report only if needed.

CLI equivalent for monitor readiness:

```powershell
python telemetry-viewer\route_monitor.py --template Bank_to_Woodcutting_area --latest-session --live --json
```

CLI equivalent for live follow:

```powershell
python telemetry-viewer\route_monitor.py --template Bank_to_Woodcutting_area --latest-session --live --follow --poll-ms 250 --out-dir "%USERPROFILE%\.osrs-telemetry\route_monitor"
```

## Remaining Limitation

The short live smoke could not prove live route progress because telemetry was
stale by about 2.2 million ms. It did prove the important fix: route monitor no
longer starts without a valid loaded template model, and a stale live source is
reported as stale rather than off-route.

## Next Recommended Task

Run one fresh route using `Start Route Session`, then compare the live route
history session and offline analyzer from the same route attempt.
