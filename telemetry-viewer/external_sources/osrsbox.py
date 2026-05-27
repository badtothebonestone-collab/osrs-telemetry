from __future__ import annotations

from pathlib import Path
from typing import Any

import external_knowledge_cache as cache


def status(cache_root: Path | None = None) -> dict[str, Any]:
    root = cache.ensure_cache(cache_root) / "osrsbox"
    return {
        "schema": "external_osrsbox_status.v1",
        "status": "WARN",
        "enabled": False,
        "cachePath": str(root),
        "available": root.exists() and any(root.glob("*.json")),
        "notes": "Optional local/static adapter placeholder. No network calls are made.",
    }
