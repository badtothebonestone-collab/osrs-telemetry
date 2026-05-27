# Current Pipeline Manifest

Schema: `osrs_telemetry_pipeline_manifest.v1`

The current pipeline is query-first and snapshot-backed:

`RuneLite plugin -> PluginSnapshotEndpoint 8893 -> WorldModelCache -> live_core_daemon 8890 -> Knowledge Fabric / MCP-direct queries -> bounded replay, script-authoring, visual debug bundles`

The retired path is the old append-only live packet archive. Normal runtime must not create or consume `live_packets\`, `live-*.ndjson`, or `live-*.jsonl`. Old files are maintenance cleanup only.

The machine-readable manifest lives at `telemetry-viewer/pipeline_manifest.json`.

| Component | Active | Runtime required | Disk behavior | Normal config keys | Replacement for old path |
| --- | --- | --- | --- | --- | --- |
| RuneLite plugin | yes | yes | bounded session manifest/dictionaries in normal mode | `enabled`, `outputDirectory` | populates in-memory cache instead of packet files |
| PluginSnapshotEndpoint 8893 | yes | yes | none | `enablePluginSnapshotEndpoint`, `pluginSnapshotHost`, `pluginSnapshotPort`, `pluginSnapshotAuthToken`, `pluginSnapshotAllowNonLocalHost` | replaces `live_packets` as live truth |
| WorldModelCache | yes | yes | none | developer hidden scene/collision caps only | replaces file-based scene packet readers |
| live daemon/context service 8890 | yes | yes | optional latest overlay state | none | replaces runtime file fallback |
| Knowledge Fabric | yes | no | explicit bounded bundles only | none | replaces grepping stale packet archives |
| MCP/local query adapter | yes | no | none | none | read-only Codex/AI inspection |
| current_debug_context | yes | no | stdout or explicit bundle JSON | none | first query for live debugging |
| replay/script-authoring/visual bundles | yes | no | explicit capped JSON/PNG bundles | none | replay/debug evidence without packet streams |
| external OSRS cache | yes | no | bounded cache under `.osrs-telemetry\external_knowledge_cache` | none | advisory static enrichment |
| Arduino/HumanInputController | yes | yes for live input | bounded input integrity status | none | only live motor-output path |
| legacy live packet archive | no | no | none | none | maintenance report/prune only |

## Plugin Settings

The normal RuneLite settings surface is intentionally small:

- Core: enable telemetry, output directory.
- Snapshot Endpoint: enable endpoint, host, port, optional token, local-host safety.
- Overlay: enable overlay, mode, max markers, labels, aimpoints, geometry/bounds.

Developer diagnostic config keys are hidden from the normal RuneLite UI. Retired recording/preset keys are hidden and cleaned on plugin startup. The active/hidden/retired key list is mirrored in `telemetry-viewer/config_keys.json`.

## Retired Runtime Outputs

These cannot be enabled as normal runtime paths:

- `live_packets\`
- `live-*.ndjson`
- `live-*.jsonl`
- compact live packet file output
- compact live stream mirroring
- raw tick/event/frame recording from the normal config UI

Use `maintenance.py --live-packets-report` and `maintenance.py --prune-legacy-live-packets --dry-run` for old archive files.
