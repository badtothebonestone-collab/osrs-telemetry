from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import external_knowledge_cache as cache


MAPPING_URL = "https://prices.runescape.wiki/api/v1/osrs/mapping"


def fetch_item_mapping(*, cache_root: Path | None = None, user_agent: str = cache.DEFAULT_USER_AGENT, timeout: float = 20.0, limit: int | None = None) -> dict[str, Any]:
    if not user_agent:
        raise ValueError("OSRS Wiki price API requests require a descriptive User-Agent")
    root = cache.ensure_cache(cache_root)
    started = time.perf_counter()
    request = urllib.request.Request(MAPPING_URL, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    rows = payload if isinstance(payload, list) else []
    if limit is not None:
        rows = rows[: max(0, int(limit))]
    existing = cache.read_json(root / "item_id_map.json", {"items": {}}) or {"items": {}}
    items: dict[str, Any] = dict(existing.get("items") or {})
    for row in rows:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        item_id = str(row.get("id"))
        items[item_id] = {
            "itemId": int(row.get("id")),
            "canonicalName": row.get("name"),
            "aliases": [row.get("name")] if row.get("name") else [],
            "examine": row.get("examine"),
            "members": row.get("members"),
            "tradeable": True,
            "stackable": row.get("stackable"),
            "highalch": row.get("highalch"),
            "lowalch": row.get("lowalch"),
            "wikiPage": f"https://oldschool.runescape.wiki/w/{str(row.get('name') or '').replace(' ', '_')}",
            "provenance": cache.provenance("osrs_wiki_prices", MAPPING_URL, confidence=0.85),
        }
    result = {
        "schema": "external_item_id_map.v1",
        "generatedAtUtc": cache.utc_now(),
        "sourceUrl": MAPPING_URL,
        "items": items,
        "count": len(items),
        "performanceStats": {"queryTimeMs": round((time.perf_counter() - started) * 1000.0, 3)},
    }
    cache.write_json(root / "item_id_map.json", result)
    cache.refresh_name_maps(root)
    status = cache.read_json(root / "source_status.json", {}) or {}
    status.setdefault("lastRefresh", {})["osrs_wiki_prices"] = cache.utc_now()
    status.setdefault("lastError", {})["osrs_wiki_prices"] = None
    cache.write_json(root / "source_status.json", status)
    return result
