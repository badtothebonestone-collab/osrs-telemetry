# Historical Reference Only

Historical reference only. Do not treat as current implementation guidance.

---

# Cleanup Report

The live stack cleanup target is complete when normal runtime uses:

```text
RuneLite plugin -> PluginSnapshotEndpoint / WorldModelCache -> live_core_daemon.py -> Knowledge Fabric / context service
```

and no normal runtime creates or reads the old append-only live packet archive.

Removed/retired:

- live packet archive writers
- live packet archive runtime readers/fallbacks
- packet stream/file mirror runtime path
- RuneLite config options that enabled packet archive output
- Python CLI flags that selected packet archive input as live truth

Preserved:

- explicit static/config JSON
- latest-state overlay/input JSON
- current debug context
- replay scenarios
- script authoring context
- data-quality reports
- visual debug bundles
- session memory
- MCP/Knowledge Fabric JSON query responses

Maintenance-only legacy report:

```powershell
python telemetry-viewer\maintenance.py --live-packets-report
```
