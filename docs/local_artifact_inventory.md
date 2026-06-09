# Local Artifact Inventory

Date: 2026-06-09

These artifacts are intentionally local and are not part of the stabilization
checkpoint commit. They are large runtime evidence folders, useful for audit and
manual inspection but not suitable for broad Git history.

| Path | Count | Size | Commit policy |
| --- | ---: | ---: | --- |
| `bot_runs/` | 127 folders, 3745 files | 546,677,503 bytes | Keep local; do not commit raw run folders by default. |
| `recordings/` | 84 folders, 2210 files | 472,242,941 bytes | Keep local; commit only curated tiny fixtures if a test explicitly requires them. |
| `telemetry-viewer/logs/` | includes `live_core_daemon_8890.out.log` | latest log alone is about 432 MB | Ignored as runtime logs. |
| `C:\Users\badto\.osrs-telemetry\sessions` | local live telemetry sessions | outside repo | Keep local; referenced in reports when needed. |
| `C:\Users\badto\.osrs-telemetry\route_monitor` | local route monitor output | outside repo | Keep local unless a small fixture is intentionally copied into tests. |
| `C:\Users\badto\.osrs-telemetry\ui_control` | UI stop files and control state | outside repo | Keep local. |

## Important Local Evidence

The following local paths are important to the regression audit and should be
preserved on this machine even though they are not committed:

- `bot_runs\20260607_204642_woodcutting_loop_eval`
- `bot_runs\20260608_081231_woodcutting_loop_live_smoke`
- `bot_runs\20260608_081923_woodcutting_loop_live_dry_run`
- `bot_runs\20260608_084813_live_woodcutting_loop`
- `bot_runs\20260608_103728_live_woodcutting_loop`
- `bot_runs\20260608_160916_live_woodcutting_loop`
- `bot_runs\20260608_173455_live_woodcutting_loop`
- `bot_runs\20260608_182824_live_woodcutting_loop`
- `bot_runs\20260608_203556_live_woodcutting_loop`
- `bot_runs\20260608_213714_live_woodcutting_loop`
- `recordings\20260607_171427_Wood_cutting_attacked`
- `recordings\20260608_160958_live_woodcutting_loop_20260608_160958`
- `recordings\20260608_182920_live_woodcutting_loop_20260608_182919`
- `recordings\20260608_203635_live_woodcutting_loop_20260608_203635`
- `C:\Users\badto\.osrs-telemetry\sessions\2026-06-09_10-04-17\interaction_geometry\live\live_baseline_state.json`

## Policy

- Commit source, tests, docs, knowledge indexes, route templates, and route
  guides.
- Do not commit broad raw `bot_runs/` or `recordings/`.
- If a future test needs a fixture, copy a small minimal fixture into a
  dedicated test fixture path and document why it is curated.
