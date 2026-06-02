# Historical Reference Only

Historical reference only. Do not treat as current implementation guidance.

---

# Pipeline Cleanup Inventory

This inventory classifies the current major data paths after the pipeline cleanup pass. Unknown or historical tools are not deleted blindly; they stay documented until references and tests prove they are safe to remove.

| Path | Classification | Import/call references | Decision | Reason | Risk | Test coverage |
| --- | --- | --- | --- | --- | --- | --- |
| `src/main/java/com/osrstelemetry/TelemetryPlugin.java` | active runtime | RuneLite plugin entry point | keep | Produces live cache, world model, client tick hot state, and bounded manifest/dictionaries | high | Gradle tests |
| `src/main/java/com/osrstelemetry/PluginSnapshotEndpoint.java` | active runtime/query | daemon, Knowledge Fabric, MCP/direct queries | keep | 8893 read-only live truth bridge | high | `PluginSnapshotEndpointTest` |
| `src/main/java/com/osrstelemetry/WorldModelCache.java` | active runtime | plugin snapshot/world model queries | keep | Loaded-scene world/collision/projection source | high | world model tests |
| `src/main/java/com/osrstelemetry/TelemetryConfig.java` | active config | RuneLite plugin settings | simplify | Normal UI now shows only core, snapshot, and overlay controls | medium | `TelemetryConfigKeysTest` |
| `src/main/java/com/osrstelemetry/TelemetryConfigKeys.java` | active config/test support | plugin startup cleanup, tests | keep | Source list for exposed/developer/retired plugin keys | low | `TelemetryConfigKeysTest` |
| `src/main/java/com/osrstelemetry/TelemetryWriter.java` | active runtime support | `TelemetryPlugin` | keep, constrain | Still owns session manifest/dictionaries/live cache facade; raw recording controls are hidden/retired from UI | medium | `TelemetryRecordingModeTest` |
| `src/main/java/com/osrstelemetry/CompactLiveStreamPublisher.java` | removed legacy | none expected | delete already staged | Old compact stream writer | low | Gradle compile/test |
| `src/main/java/com/osrstelemetry/LivePacket.java` | removed legacy | none expected | delete already staged | Old live packet archive model | low | Gradle compile/test |
| `src/main/java/com/osrstelemetry/LivePacketWriter.java` | removed legacy | none expected | delete already staged | Old append-only packet writer | low | Gradle compile/test |
| `telemetry-viewer/live_core_daemon.py` | active runtime | daily daemon 8890 | keep | In-memory context service and analyzers | high | stabilization suite |
| `telemetry-viewer/context_service.py` | active query | CLI, 8890-compatible context service, Knowledge Fabric | keep | Direct query, bundle, replay, pipeline health | high | `test_context_service.py` |
| `telemetry-viewer/knowledge_fabric.py` | active query/debug | context service, MCP, Codex | keep | Query-first debug and script authoring surface | high | `test_knowledge_fabric.py` |
| `telemetry-viewer/mcp_server.py` | active query/debug | Codex local MCP adapter | keep | Read-only MCP-style query access | medium | py_compile/list-tools validation |
| `telemetry-viewer/world_model_core.py` / `world_model_client.py` | active runtime/query | Knowledge Fabric/live daemon | keep | World model request/summary helpers | high | world model tests |
| `telemetry-viewer/input_control/*` | active runtime/input | executor/bootstrap | keep | Arduino/HumanInputController/input integrity and target-view recovery | high | stabilization suite |
| `telemetry-viewer/target_view_core.py` | active runtime/query | action proposal/executor/view quality | keep | Generic target exposure and camera policy | medium | `test_target_view_core.py` |
| `telemetry-viewer/external_knowledge*.py` | active advisory/query | task probe, current debug context | keep | Cache-first advisory OSRS facts | low | external knowledge tests |
| `telemetry-viewer/maintenance.py` | maintenance only | explicit cleanup/report commands | keep | Only approved legacy live packet report/prune path | low | `test_maintenance.py` |
| `telemetry-viewer/inspect_live_packets.py` | legacy compatibility | stabilization test shim | keep as legacy pointer | Reports archive retirement and replacement commands | low | `test_inspect_live_packets.py` |
| `telemetry-viewer/live_packet_reader.py` | removed legacy | none expected | delete already staged | Old live packet archive reader/fallback | low | stabilization suite |
| `telemetry-viewer/live_target_processor.py` | active/legacy hybrid | daemon/tests/static file diagnostics | keep for now | Still used by analyzer/tests and legacy diagnostics; archive reader portions are report-only | medium | stabilization suite |
| `telemetry-viewer/live_config_doctor.py` / `check_live_setup.py` | active maintenance diagnostics | setup/gauntlet/tests | keep | Reports legacy archives as warnings, not live truth | low | tests |
| `telemetry-viewer/live_control_panel.py` | active debug/UI | manual control panel/tests | keep | Daily command/control panel, legacy archive status as report only | medium | tests |
| `telemetry-viewer/pipeline_manifest.json` | active docs/query | pipeline health | keep | Machine-readable current pipeline source | low | pipeline health tests |
| `telemetry-viewer/config_keys.json` | active docs/query | pipeline health/docs | keep | Machine-readable config key inventory | low | pipeline health tests |
| `interaction_geometry/live/replay_scenarios` | explicit bounded debug artifact | replay validation | keep | User-triggered offline replay evidence | low | Knowledge Fabric tests |
| `interaction_geometry/live/script_authoring_context` | explicit bounded debug artifact | task authoring | keep | User-triggered script context | low | Knowledge Fabric tests |

## Cleanup Decisions

- Normal plugin UI no longer exposes workflow presets, raw tick/event recording, frame capture, compact packet labels, or the normal-live snapshot alias.
- Old archive writer classes and live packet reader remain removed.
- Existing maintenance/report tools may mention `live_packets` only as legacy cleanup.
- JSONL in older offline training/session diagnostics is not deleted in this pass because it is outside the removed `live_packets` runtime archive and still has tests.
