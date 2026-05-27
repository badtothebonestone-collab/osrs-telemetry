from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "schema": "legacy_live_packet_inspection_retired.v1",
        "status": "FAIL",
        "reason": "live packet NDJSON/JSONL archive inspection is retired",
        "replacement": {
            "currentState": "python telemetry-viewer\\context_service.py --query current-debug-context",
            "currentBlocker": "python telemetry-viewer\\context_service.py --query explain-current-blocker",
            "replay": "python telemetry-viewer\\context_service.py --capture-replay-scenario --profile woodcutting --reason <reason>",
            "scriptAuthoring": "python telemetry-viewer\\context_service.py --capture-script-authoring-context --profile woodcutting",
            "legacyCleanupReport": "python telemetry-viewer\\maintenance.py --live-packets-report",
        },
        "livePacketsRuntimeRemoved": True,
        "ndjsonRuntimeRemoved": True,
        "jsonlRuntimeRemoved": True,
        "livePacketWriterActive": False,
    }
    print(json.dumps(payload, indent=2))
    return 2


if __name__ == "__main__":
    sys.exit(main())
