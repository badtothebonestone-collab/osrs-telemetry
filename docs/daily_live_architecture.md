# Daily Live Architecture

Snapshot No-File is the daily architecture.

```text
RuneLite plugin
-> PluginSnapshotEndpoint / WorldModelCache
-> live_core_daemon.py
-> Knowledge Fabric / context service
-> readiness / action proposal / overlay
```

The retired append-only packet archive is not a daily path:

- no `live_packets\` runtime directory
- no `live-*.ndjson` or `live-*.jsonl` packet files
- no compact packet file-source daemon mode
- no compact stream/file mirror runtime mode
- no RuneLite config switch to enable those outputs

Canonical daemon command:

```powershell
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

Daily diagnostics:

```powershell
python telemetry-viewer\context_service.py --query current-debug-context
python telemetry-viewer\context_service.py --query explain-current-blocker
python telemetry-viewer\diagnose_live_readiness.py --latest-session --daemon-url http://127.0.0.1:8890 --profile woodcutting
python telemetry-viewer\run_daily_gauntlet.py --latest-session --daemon-url http://127.0.0.1:8890 --daily-mode snapshot-no-files --strict --check-processes
```

Explicit bounded JSON/debug outputs remain valid: overlay state, input
integrity, current debug context, replay scenarios, script authoring context,
data-quality reports, debug diffs, handoff summaries, visual debug bundles, and
session memory.

Old packet files are legacy disk cleanup only:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
```
