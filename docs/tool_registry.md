# Tool Registry Notes

This is the human-readable daily tool summary. The machine-readable registry is
`telemetry-viewer\tool_registry.json`.

## Current Daily Flow

```text
RuneLite plugin -> PluginSnapshotEndpoint / WorldModelCache -> live_core_daemon.py -> Knowledge Fabric -> readiness/action proposal
```

## Canonical Commands

| Tool | Purpose | Command |
| --- | --- | --- |
| `live_core_daemon.py` | Snapshot No-File daemon on `8890`. | `python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark` |
| `context_service.py` | Query-first current context and blocker explanations. | `python telemetry-viewer\context_service.py --query current-debug-context` |
| `task_script_api.py` | High-level task script primitive contract, validation, compile, evidence planning, runtime evidence snapshots/comparison, failure classification, explanation, and templates. | MCP: `get_task_script_api_spec`, `validate_task_script`, `compile_task_script`, `get_task_script_evidence_plan`, `get_task_script_runtime_evidence`, `compare_task_script_runtime_evidence`, `classify_task_failure` |
| `context_service.py --handoff-summary` | Redacted `PASTE_TO_CHATGPT` block for bounded ChatGPT consultation after local tools are insufficient. | `python telemetry-viewer\context_service.py --handoff-summary` |
| `diagnose_live_readiness.py` | Action readiness and execution gate diagnostics. | `python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting` |
| `run_daily_gauntlet.py` | Strict daily sanity check. | `python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes` |
| `maintenance.py` | Legacy live packet disk report/prune only. | `python telemetry-viewer\maintenance.py --live-packets-report` |

## Removed Tooling

`inspect_live_packets.py` is a retired compatibility shim. The old packet
reader/runtime fallback is gone; use maintenance for old disk files and
Knowledge Fabric/current debug context for live state.

No current canonical command should enable a live packet archive, packet stream,
or packet-file fallback.

## ChatGPT Consultation

Use local query/test evidence first. If a real blocker, architecture question,
or safety/input decision remains, prefer Chrome Use in the already-open ChatGPT
conversation. Use Computer Use only as fallback, and print
`PASTE_TO_CHATGPT` manually if both UI paths fail. See
`docs\chatgpt_consultation_workflow.md`.
