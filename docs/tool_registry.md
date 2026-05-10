# Tool Registry

This project now has two clear lanes:

- **Normal live** uses compact live packets, rolling live files, the context
  service, the human dashboard, and optional visual QA.
- **Debug/audit** uses full raw tick/event/frame recordings for batch builders,
  replay, dataset generation, and deep inspection.

The machine-readable registry lives at:

```text
telemetry-viewer\tool_registry.json
```

## Normal Live

| Tool | Purpose | Example |
| --- | --- | --- |
| `live_control_panel.py` | Main everyday launcher and process monitor. | `python telemetry-viewer\live_control_panel.py` |
| `check_live_setup.py` | Verifies compact packets, recording mode, and rolling live readiness. | `python telemetry-viewer\check_live_setup.py --latest-session --require-compact-packets` |
| `inspect_live_packets.py` | Summarizes compact packet segments without scanning raw ticks. | `python telemetry-viewer\inspect_live_packets.py --latest-session --summary` |
| `live_target_processor.py` | Consumes compact packets and writes rolling live context files. | `python telemetry-viewer\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow` |
| `context_service.py` | Local read-only context_request.v1 API. | `python telemetry-viewer\context_service.py --latest-session --port 8890` |
| `live_context_query.py` | Human dashboard, event timeline, and context query helper. | `python telemetry-viewer\live_context_query.py --latest-session --task woodcutting --human --events 10` |
| `mock_brain_rehearsal.py` | Read-only future-brain rehearsal client. | `python telemetry-viewer\mock_brain_rehearsal.py --task woodcutting --goal-count 5 --human` |

## Visual QA

| Tool | Purpose |
| --- | --- |
| RuneLite Telemetry Debug Overlay | Optional read-only overlay for candidates, aim points, reachability, and status. |
| `target_geometry_inspector.py` | Browser-based live or recorded target geometry inspection. |
| `diagnose_overlay_state.py` | Compares overlay state against live candidates/context. |

## Debug / Audit

| Tool | Purpose |
| --- | --- |
| `run_target_geometry_pipeline.py` | Full batch target geometry pipeline for DEBUG_RECORDING sessions. |
| `build_world_target_geometry.py` | Builds world target geometry from raw/debug session data. |
| `build_ui_target_geometry.py` | Builds UI target geometry from raw/debug session data. |
| `select_target_candidates.py` | Selects target candidates from batch geometry outputs. |
| `diagnose_target_coverage.py` | Audits target/profile coverage from recorded sessions. |
| Dataset builders and inspectors | `build_*dataset.py`, `prepare_visual_perception.py`, and inspector scripts remain debug/training tools. |

## Legacy Compatibility

`telemetry_launcher.py` is retained for compatibility. New daily work should use
`live_control_panel.py`.
