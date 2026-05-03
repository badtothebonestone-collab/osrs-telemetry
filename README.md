# OSRS Telemetry

Read-only RuneLite telemetry collector.

Telemetry sessions use the segmented layout as the canonical writer output:

```text
C:\Users\stone\.osrs-telemetry\sessions\<session_id>\
  manifest.json
  ticks\ticks-*.jsonl
  events\events-*.jsonl
  frames\frame-tick-XXXXXXXX.jpg
  dictionaries\
  latest\
  exports\
```

The viewer tools also support older read-only sessions that used flat
`ticks.jsonl` and `events.jsonl` files.

To view the newest session:

```powershell
python telemetry-viewer\viewer.py
```

## Telemetry Control Center

Launch the local Tkinter control center from the project root:

```powershell
python telemetry-viewer\telemetry_launcher.py
```

The launcher starts and stops local dev tools and read-only telemetry scripts.
It defaults to:

```text
C:\Users\stone\.osrs-telemetry\sessions
```

You can override the sessions directory in the GUI. The launcher passes that
value to child tools through `OSRS_TELEMETRY_SESSIONS_DIR`.

Buttons:

- Start Core Stack: starts the RuneLite dev client, latest-state watcher, and
  replay viewer if they are not already running, then opens the replay URL.
- Start RuneLite Dev Client: runs the local Gradle dev client command.
- Start Text Viewer: starts `telemetry-viewer\viewer.py`.
- Start Latest State Watcher: starts `telemetry-viewer\latest_state.py`.
- Start Replay Viewer: starts `telemetry-viewer\replay_viewer.py`.
- Open Replay Viewer in Browser: opens `http://127.0.0.1:8765/`.
- Run Validate Session: runs `telemetry-viewer\validate_session.py`.
- Run Export Session: writes generated summaries with `telemetry-viewer\export_session.py`.
- Run Path Regression Tests: runs `telemetry-viewer\test_telemetry_paths.py`.
- Open Sessions Folder: opens the configured sessions directory.
- Open Newest Session Folder: opens the newest discovered session.
- Stop Selected Process: stops a process started by this launcher.
- Stop All Started Processes: stops all processes started by this launcher.
- Clear Log: clears the launcher log panel.

The Telemetry Health panel shows the newest session path, active status, latest
tick id and age, game state, position, HP/prayer/run, tick/event/frame file
counts, frame and session sizes, capture errors, and the last validation result.

Health status colors:

- OK: active session and latest tick age is under 10 seconds.
- Warning: latest tick age is 10-60 seconds.
- Stale: latest tick is over 60 seconds old or no ticks were found.

Health quick actions open existing telemetry files without creating or editing
them:

- Open latest frame file
- Open `latest_status.json`
- Open `manifest.json`
- Open newest tick segment
- Open newest event segment
- Open newest session folder

Safety: the launcher only manages processes it started. It does not perform
game automation, clicking, input hooks, overlays, menu actions, or client-state
mutation.

On Windows, Stop Selected Process and Stop All Started Processes stop the
launcher-started process tree by PID with `taskkill /T /F`. The launcher does
not kill unrelated Java, Gradle, RuneLite, or Python processes by image name.
