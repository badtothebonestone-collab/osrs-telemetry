# Runtime Cleanup Report

Current cleanup state:

- Snapshot No-File is the normal daily path.
- Plugin snapshot endpoint and WorldModelCache provide live data.
- Knowledge Fabric and MCP-style tools provide read-only query access.
- The live packet NDJSON/JSONL archive is retired and cannot be enabled.
- Explicit bounded JSON context/debug/replay artifacts remain valid.

Canonical daemon:

```powershell
python telemetry-viewer\live_core_daemon.py --latest-session --profile woodcutting --daily-mode snapshot-no-files --input-source plugin-snapshot --plugin-snapshot-tier hot --preset woodcut_bank --goal-count 5 --context-port 8890 --write-overlay-state --overlay-mode intent --overlay-backup-candidates 2 --overlay-debug-target-limit 32 --human-dashboard --summary --benchmark
```

Legacy disk cleanup:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --dry-run
```

Deletion requires:

```powershell
python telemetry-viewer\maintenance.py --prune-legacy-live-packets --apply
```
