# RuneLite Telemetry Plugin Development — Agent Guidelines

## How To Use This File

* Read this file before making changes.
* Also read `docs/codex\_handoff\_current.md` for the current project state, current milestone, and live QA workflow.
* Do not rely on old chat history as source of truth. Use the current repo, current tests, current diagnostics, `AGENTS.md`, and `docs/codex\_handoff\_current.md`.
* Keep changes focused on the requested milestone.
* Do not mix broad refactors, formatting-only changes, and feature changes in one pass unless the user explicitly asks for that.

## Current Project Preferences

* Preserve Snapshot No-File as the daily path unless the user explicitly asks to change it.
* Avoid new continuous runtime JSON/NDJSON outputs.
* Do not reintroduce scanner/checker/filter systems that inspect field names.
* Do not strip useful telemetry/context fields because of names like `actions`, `menuActions`, `actionNames`, `clickbox`, `target`, `path`, `interaction`, `destination`, `waypoint`, or similar.
* Filtering should only happen for explicit performance, size, display, or task-selection reasons.
* If a proposed filter/removal is not obviously performance, size, display, or task-selection related, ask before implementing it.
* Keep Mission Control, daemon, diagnostics, overlay, Snapshot No-File, and stabilization-suite workflows intact unless the current task explicitly requires changing them.
* Prefer small, testable changes with clear diagnostics over large architecture rewrites.

## Current Daily Live Command

The current normal daemon command is:

```powershell
python telemetry-viewer\\live\_core\_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut\_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

## Current Diagnostic Commands

Useful live diagnostics:

```powershell
python telemetry-viewer\\diagnose\_service\_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\\diagnose\_pathing\_context.py --from-daemon --daemon-url http://127.0.0.1:8890
python telemetry-viewer\\diagnose\_overlay\_state.py --latest-session --intent
python telemetry-viewer\\diagnose\_task\_transition.py --from-daemon --daemon-url http://127.0.0.1:8890 --policy woodcutting\_bank
python telemetry-viewer\\run\_daily\_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
```

Useful endpoint check:

```powershell
$request = @{
  schema = "plugin\_snapshot\_request.v1"
  needs = @("baseline", "writer\_health")
  maxAgeTicks = 5
  responseMode = "compact"
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8893/snapshot" -Body $request -ContentType "application/json"
```

## Verification Commands

Always run focused tests for the files changed.

Always run:

```powershell
python telemetry-viewer\\run\_stabilization\_suite.py
```

If Java/plugin code changed, also run:

```powershell
.\\gradlew.bat test
.\\gradlew.bat build
```

If Python daemon/analyzer/diagnostic files changed, run relevant `py\_compile` checks, for example:

```powershell
python -m py\_compile telemetry-viewer\\live\_core\_daemon.py
python -m py\_compile telemetry-viewer\\diagnose\_service\_context.py
python -m py\_compile telemetry-viewer\\diagnose\_pathing\_context.py
```

## Live QA / Computer Use Workflow

Codex may run terminal commands from the plugin root.

Preferred live QA flow:

1. Launch RuneLite dev if needed:

```powershell
   .\\gradlew.bat run
   ```

2. Wait for the plugin snapshot endpoint on `127.0.0.1:8893`.
3. If RuneLite needs manual login, account confirmation, or anything credential-related, ask the user to handle it.
4. Once the user is logged in and the plugin endpoint is responding, continue automatically.
5. Wait until plugin snapshot reports `LOGGED\_IN`.
6. Start or restart `live\_core\_daemon.py`.
7. Run the relevant diagnostics and gauntlet.

If Computer Use can access the RuneLite dev window, Codex may click simple already-authenticated buttons such as Play, Log in, or Continue. If Computer Use cannot access the RuneLite window, ask the user to click/log in manually and then continue with endpoint checks and diagnostics.

For preferred window placement:

* If the RuneLite dev client opens on the wrong monitor, try moving the active window to the other monitor with `Windows+Shift+Left` or `Windows+Shift+Right`.
* If this cannot be done reliably, ask the user to move the window manually.

## Logging

* Use `log.debug()` for developer/diagnostic logging.
* Do not use `log.info()` for per-frame or per-event logging. RuneLite runs at INFO level in production, so high-frequency info logs will pollute user logs.
* `log.info()` is fine for one-time startup/shutdown messages or infrequent events.

## Threading \& Concurrency

* Never use `Thread.sleep()`.
* Never block on `shutDown()` or `startUp()`.
* Do not call `executor.awaitTermination()` in shutdown; use `shutdownNow()`.
* Never do blocking network IO or disk IO on the client thread.
* The OkHttp thread pool can be used for blocking network requests.
* If you need to call back into `client` from the OkHttp thread pool, such as from a response queued with `enqueue()`, use `clientThread.invoke()`.
* Explicitly cancel scheduled tasks, such as `ScheduledFuture`, on shutdown in addition to shutting down the executor.
* For batching async work, use `CompletableFuture.allOf()`, not `CountDownLatch`.
* If you must use `Process.waitFor()`, always pass a reasonable timeout.

## Performance

* Do not scan the entire scene every tick or frame unless the current task explicitly requires a bounded diagnostic/audit pass.
* Prefer event-driven object/NPC tracking where practical.
* Keep overlay computations minimal because overlays run every frame.
* For live daily mode, preserve Snapshot No-File behavior and avoid reintroducing per-tick file writes.
* Keep pathing, service, inventory, and UI analyzers bounded.
* Prefer compact, task-relevant payloads over huge generic dumps.
* If a cap or compact mode hides useful data, expose a diagnostic that explains what was capped and why.

## API Usage

* Use `net.runelite.api.gameval` package constants such as `ItemID`, `InterfaceID`, and `ObjectID` when available.
* Avoid hardcoded magic numbers when gameval constants can be used instead.
* Use `LinkBrowser` to open URLs, not `java.awt.Desktop`.
* When looking up widgets, pass the component ID from gamevals when available, for example `client.getWidget(InterfaceID.DomEndLevelUi.LOOT\_VALUE)`.
* Do not manually combine interface and component child IDs when a gameval component ID exists.
* Java reflection is forbidden.

## HTTP \& JSON

* Use OkHttp for all HTTP requests.
* Use `@Inject OkHttpClient` to get the HTTP client.
* Do not use `HttpURLConnection`, `java.net.http.HttpClient`, or Apache HttpClient.
* Use `@Inject Gson` to get a Gson instance.
* Do not create a base Gson from scratch.
* You may use `.newBuilder()` to create a derived Gson from the injected base `Gson`.
* Do not add transitive dependencies from `runelite-client` directly to `build.gradle`, such as gson, guice, or okhttp.
* Never execute OkHttp calls on the client thread.
* Prefer `enqueue()` for requests that should run on the OkHttp thread pool.

## File I/O

* Daily live mode should not create continuous JSON/NDJSON runtime outputs.
* If file output is needed for a one-shot diagnostic, it must be explicit and user-triggered.
* Plugin-side file I/O should stay inside the `.runelite` directory.
* Use `RuneLite.RUNELITE\_DIR` to get the path.
* Create a plugin-specific subdirectory under `.runelite` if plugin storage is needed.
* Alternatively, use `JFileChooser` for user-initiated file operations.
* Do not commit build artifacts, temporary outputs, or generated live session spam.

## Config

* Config group names must be specific, for example `"osrs-telemetry"`, not a generic name.
* Never rename a config key or config group without providing a migration. Renaming silently resets users' saved settings.
* Keep Daily Snapshot No-File presets working.
* Config/preset changes should not unexpectedly re-enable compact packet file writes, raw ticks/events/frames, screenshots, crops, or old debug pipelines.
* If a config option affects runtime output volume, endpoint payload size, or overlay clutter, document the default and the reason.

## Plugin Setup \& Packaging

* Rename everything from the template. Do not leave `com.telemetry`, `ExamplePlugin`, `ExampleConfig`, or `example` as the config group.
* Rename package path, class names, config group, `build.gradle` group, `settings.gradle` project name, and `runelite-plugin.properties` when applicable.
* Do not include a `META-INF/services/net.runelite.client.plugins.Plugin` file.
* Do not commit build artifacts such as `.class` files, `out/` directories, `.tmp` directories, or Gradle build outputs.
* `build.gradle` must target Java 11 and match the structure of the RuneLite example-plugin template.
* Retain a permissive license, such as BSD-2.

## Resources \& Assets

* Optimize icon PNGs.
* Java loads images at full resolution in memory using roughly `width × height × 4` bytes, so visually small files can still use significant memory.
* Ensure PNGs are actually PNGs. Do not rename JPEGs or ICOs to `.png`.

## Cleanup

* Remove unused config classes, fields, and imports.
* Clean up subscriptions, listeners, overlays, executors, and scheduled tasks in `shutDown()`.
* Do not mix code reformatting with feature changes in the same commit.
* Keep old/legacy tools documented or moved only when the current task is specifically cleanup-related.
* If deleting or quarantining a file, explain why it is no longer part of the daily path.

## Python Sidecar / Analyzer Conventions

* Keep analyzers focused and in-memory.
* Prefer one analyzer per domain:

  * inventory
  * target/service
  * navigation/pathing
  * task state
  * bank UI
  * overlay intent
* Avoid adding new background services unless the task clearly requires one.
* Diagnostics should print to stdout by default.
* `--json` diagnostics should print JSON to stdout by default.
* Optional file output should require an explicit `--output` path.
* Use clear diagnostic fields for:

  * source tick
  * freshness
  * missing capabilities
  * warnings
  * timing
  * retained/stabilized state where relevant
* Do not hide useful telemetry fields because of field names.

## Overlay Conventions

* Separate selected target geometry from path tile geometry.
* Selected target geometry should not be dropped just because path markers increase.
* Path tile overlay should use stable world-tile keys.
* Destination, final approach, next waypoint, and predicted path tiles should remain distinguishable.
* Daily overlay should stay readable.
* Debug/visual QA overlay may show more detail.
* Overlay diagnostics should explain marker counts, caps, geometry sources, and fallback behavior.

## Service / Pathing Baseline

Current known-good behavior to preserve unless the user explicitly asks to change it:

* `woodcut\_bank` uses full-bank service preference.
* Bank booth / banker / bank chest are primary full-bank service targets.
* Deposit box / deposit chest are fallback targets.
* Generic bank-related scenery is lower priority.
* Bank booth wins over Deposit Box when visible.
* Retained Bank booth blocks Deposit Box fallback when the current candidate lane drops the booth.
* Collision window cache works.
* OSRS-like predicted path mode works.
* Path tile overlay works.
* Path intent stabilization works.
* Arrival/serviceReady layer was added.

## Task Handoff

* Keep `docs/codex\_handoff\_current.md` updated with:

  * current known-good baseline
  * current daemon command
  * current diagnostics
  * current next milestone
  * live QA workflow
* New Codex chats should read `AGENTS.md` first, then `docs/codex\_handoff\_current.md`.
* Do not paste multi-week chat history into new tasks unless specifically needed.
* Use concise current diagnostics instead of old conversation context.

## Response / Summary Expectations

When finishing a task, summarize:

* Files changed
* Root cause, if a bug was fixed
* Behavior changed
* Commands/tests run
* Any tests not run and why
* Exact live retest commands
* Any remaining caveats

Do not claim live behavior is confirmed unless it was actually tested through the live diagnostics or the user confirmed it in-game.

