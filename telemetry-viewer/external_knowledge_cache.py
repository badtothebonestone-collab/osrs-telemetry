from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_REGISTRY_SCHEMA = "external_knowledge_sources.v1"
CACHE_STATUS_SCHEMA = "external_knowledge_cache_status.v1"
DEFAULT_MAX_CACHE_MB = 500.0
DEFAULT_USER_AGENT = "osrs-telemetry-codex/1.0 (local VM dev; cache-first external knowledge)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cache_root() -> Path:
    override = os.environ.get("OSRS_TELEMETRY_EXTERNAL_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".osrs-telemetry" / "external_knowledge_cache"


def normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def read_json(path: Path, fallback: Any | None = None) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return fallback
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def directory_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def provenance(source_id: str, source_url: str | None = None, *, confidence: float = 0.75, fetched_at: str | None = None) -> dict[str, Any]:
    return {
        "source": source_id,
        "sourceId": source_id,
        "sourceUrl": source_url,
        "fetchedAt": fetched_at or utc_now(),
        "confidence": confidence,
        "stale": False,
        "liveOverridden": False,
    }


def default_source_inventory(root: Path | None = None) -> dict[str, Any]:
    root = root or cache_root()
    return {
        "schema": SOURCE_REGISTRY_SCHEMA,
        "generatedAtUtc": utc_now(),
        "defaultCachePath": str(root),
        "apiEnabledByDefault": False,
        "hotRuntimeExternalApiCallsAllowed": False,
        "maxCacheMb": DEFAULT_MAX_CACHE_MB,
        "sources": [
            {
                "sourceId": "osrs_wiki_mediawiki",
                "sourceName": "OSRS Wiki MediaWiki API",
                "sourceType": "mediawiki_api",
                "basePurpose": "Wiki page lookup, static object/item/NPC/location facts, and script-authoring explanations.",
                "freshnessClass": "slow_changing",
                "queryCapabilities": ["page_lookup", "search", "infobox_text", "requirements", "locations"],
                "cachePath": str(root / "wiki_page_cache"),
                "rateLimitPolicy": "cache-first; explicit refresh only; serial requests with backoff",
                "userAgentRequired": True,
                "enabled": True,
                "lastRefresh": None,
                "lastError": None,
                "trustLevel": "advisory_static",
                "notes": "Never used as live click truth. Live RuneLite evidence wins.",
            },
            {
                "sourceId": "osrs_wiki_prices",
                "sourceName": "OSRS Wiki Real-Time Prices API",
                "sourceType": "real_time_prices_api",
                "basePurpose": "Item ID/name mapping and optional price metadata for value-aware scripts.",
                "freshnessClass": "price_dynamic",
                "queryCapabilities": ["item_mapping", "latest_prices"],
                "cachePath": str(root / "item_id_map.json"),
                "rateLimitPolicy": "explicit refresh only; no hot-loop use",
                "userAgentRequired": True,
                "enabled": True,
                "lastRefresh": None,
                "lastError": None,
                "trustLevel": "advisory_metadata",
                "notes": "Woodcutting execution does not depend on price data.",
            },
            {
                "sourceId": "manual_static_library",
                "sourceName": "Project static libraries",
                "sourceType": "manual_curated",
                "basePurpose": "Target classes, service routes, skill requirements, and route priors reviewed in-repo.",
                "freshnessClass": "versioned",
                "queryCapabilities": ["target_profiles", "target_library", "service_routes", "skill_requirements"],
                "cachePath": str(root),
                "rateLimitPolicy": "local files only",
                "userAgentRequired": False,
                "enabled": True,
                "lastRefresh": None,
                "lastError": None,
                "trustLevel": "advisory_prior",
                "notes": "Static priors remain advisory until live world model verifies actionability.",
            },
            {
                "sourceId": "osrsbox_static",
                "sourceName": "OSRSBox/static database adapters",
                "sourceType": "static_database",
                "basePurpose": "Optional local item/NPC/object metadata if installed later.",
                "freshnessClass": "versioned",
                "queryCapabilities": ["items", "npcs", "monsters", "objects"],
                "cachePath": str(root / "osrsbox"),
                "rateLimitPolicy": "local cache only unless explicitly populated",
                "userAgentRequired": False,
                "enabled": False,
                "lastRefresh": None,
                "lastError": None,
                "trustLevel": "optional_advisory",
                "notes": "Placeholder source; not required for current runtime.",
            },
        ],
    }


def _seed_item_id_map() -> dict[str, Any]:
    fetched = "manual_seed"
    return {
        "schema": "external_item_id_map.v1",
        "generatedAtUtc": utc_now(),
        "items": {
            "1511": {
                "itemId": 1511,
                "canonicalName": "Logs",
                "aliases": ["logs", "normal logs"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Logs",
                "tradeable": True,
                "stackable": False,
                "examine": "A number of wooden logs.",
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Logs", confidence=0.8, fetched_at=fetched),
            },
            "1521": {
                "itemId": 1521,
                "canonicalName": "Oak logs",
                "aliases": ["oak logs"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Oak_logs",
                "tradeable": True,
                "stackable": False,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Oak_logs", confidence=0.8, fetched_at=fetched),
            },
            "1519": {
                "itemId": 1519,
                "canonicalName": "Willow logs",
                "aliases": ["willow logs"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Willow_logs",
                "tradeable": True,
                "stackable": False,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Willow_logs", confidence=0.8, fetched_at=fetched),
            },
        },
    }


def _seed_object_knowledge() -> dict[str, Any]:
    fetched = "manual_seed"
    return {
        "schema": "external_object_knowledge.v1",
        "generatedAtUtc": utc_now(),
        "objects": {
            "tree": {
                "canonicalName": "Tree",
                "aliases": ["basic tree", "normal tree", "dead tree"],
                "actions": ["Chop down"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Tree",
                "requiredSkill": "WOODCUTTING",
                "requiredLevel": 1,
                "futureEligibleWhenLevelMet": True,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Tree", confidence=0.75, fetched_at=fetched),
            },
            "dead tree": {
                "canonicalName": "Dead tree",
                "aliases": ["dead tree"],
                "actions": ["Chop down"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Dead_tree",
                "requiredSkill": "WOODCUTTING",
                "requiredLevel": 1,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Dead_tree", confidence=0.75, fetched_at=fetched),
            },
            "oak": {
                "canonicalName": "Oak",
                "aliases": ["oak tree"],
                "actions": ["Chop down"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Oak",
                "requiredSkill": "WOODCUTTING",
                "requiredLevel": 15,
                "visibleButNotExecutableUntilRequirementMet": True,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Oak", confidence=0.78, fetched_at=fetched),
            },
            "staircase": {
                "canonicalName": "Staircase",
                "aliases": ["stairs", "ladder"],
                "actions": ["Climb-up", "Climb-down"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Staircase",
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Staircase", confidence=0.65, fetched_at=fetched),
            },
            "bank booth": {
                "canonicalName": "Bank booth",
                "aliases": ["bank", "booth"],
                "actions": ["Bank"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Bank_booth",
                "serviceType": "bank",
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Bank_booth", confidence=0.7, fetched_at=fetched),
            },
        },
    }


def _seed_skill_requirements() -> dict[str, Any]:
    return {
        "schema": "external_skill_requirements.v1",
        "generatedAtUtc": utc_now(),
        "requirements": {
            "tree": {"target": "Tree", "requiredSkill": "WOODCUTTING", "requiredLevel": 1, "provenance": provenance("manual_static_library", confidence=0.85, fetched_at="manual_seed")},
            "dead tree": {"target": "Dead tree", "requiredSkill": "WOODCUTTING", "requiredLevel": 1, "provenance": provenance("manual_static_library", confidence=0.85, fetched_at="manual_seed")},
            "oak": {"target": "Oak", "requiredSkill": "WOODCUTTING", "requiredLevel": 15, "provenance": provenance("manual_static_library", confidence=0.85, fetched_at="manual_seed")},
            "oak tree": {"target": "Oak", "requiredSkill": "WOODCUTTING", "requiredLevel": 15, "provenance": provenance("manual_static_library", confidence=0.85, fetched_at="manual_seed")},
            "willow": {"target": "Willow", "requiredSkill": "WOODCUTTING", "requiredLevel": 30, "provenance": provenance("manual_static_library", confidence=0.8, fetched_at="manual_seed")},
        },
    }


def _seed_locations() -> dict[str, Any]:
    return {
        "schema": "external_location_knowledge.v1",
        "generatedAtUtc": utc_now(),
        "locations": {
            "lumbridge castle bank": {
                "canonicalName": "Lumbridge Castle bank",
                "aliases": ["lumbridge bank", "castle bank"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Lumbridge",
                "staticCoordinates": {"worldX": 3208, "worldY": 3220, "plane": 2},
                "areaBounds": {"minX": 3200, "maxX": 3212, "minY": 3215, "maxY": 3230, "planes": [2]},
                "serviceType": "bank",
                "confidence": 0.45,
                "advisoryOnly": True,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Lumbridge", confidence=0.45, fetched_at="manual_seed"),
            },
            "lumbridge west trees": {
                "canonicalName": "Lumbridge west trees",
                "aliases": ["lumbridge west castle trees", "west lumbridge trees"],
                "wikiPage": "https://oldschool.runescape.wiki/w/Lumbridge",
                "staticCoordinates": {"worldX": 3196, "worldY": 3248, "plane": 0},
                "areaBounds": {"minX": 3188, "maxX": 3210, "minY": 3235, "maxY": 3258, "planes": [0]},
                "confidence": 0.45,
                "advisoryOnly": True,
                "provenance": provenance("manual_static_library", "https://oldschool.runescape.wiki/w/Lumbridge", confidence=0.45, fetched_at="manual_seed"),
            },
        },
    }


def ensure_cache(root: Path | None = None) -> Path:
    root = root or cache_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "wiki_page_cache").mkdir(exist_ok=True)
    (root / "map_static_cache").mkdir(exist_ok=True)
    seeds = {
        "external_knowledge_sources.json": default_source_inventory(root),
        "item_id_map.json": _seed_item_id_map(),
        "object_knowledge.json": _seed_object_knowledge(),
        "npc_knowledge.json": {"schema": "external_npc_knowledge.v1", "generatedAtUtc": utc_now(), "npcs": {}},
        "skill_requirements.json": _seed_skill_requirements(),
        "location_knowledge.json": _seed_locations(),
        "source_status.json": {
            "schema": "external_source_status.v1",
            "generatedAtUtc": utc_now(),
            "lastRefresh": {},
            "lastError": {},
            "apiEnabledByDefault": False,
            "hotRuntimeExternalApiCallsAllowed": False,
        },
    }
    for filename, payload in seeds.items():
        path = root / filename
        if not path.exists():
            write_json(path, payload)
    if not (root / "item_name_map.json").exists():
        refresh_name_maps(root)
    return root


def refresh_name_maps(root: Path | None = None) -> dict[str, Any]:
    root = root or ensure_cache()
    item_map = read_json(root / "item_id_map.json", {"items": {}}) or {"items": {}}
    by_name: dict[str, Any] = {}
    for item_id, item in (item_map.get("items") or {}).items():
        if not isinstance(item, dict):
            continue
        names = [item.get("canonicalName"), *list(item.get("aliases") or [])]
        for name in names:
            key = normalize_key(name)
            if key:
                by_name[key] = {**item, "itemId": item.get("itemId") or int(item_id)}
    payload = {"schema": "external_item_name_map.v1", "generatedAtUtc": utc_now(), "itemsByName": by_name}
    write_json(root / "item_name_map.json", payload)
    return payload


def cache_status(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    total_bytes = directory_size_bytes(root)
    source_status = read_json(root / "source_status.json", {}) or {}
    return {
        "schema": CACHE_STATUS_SCHEMA,
        "status": "PASS" if (total_bytes / (1024 * 1024)) <= DEFAULT_MAX_CACHE_MB else "WARN",
        "cachePath": str(root),
        "cacheSizeBytes": total_bytes,
        "cacheSizeMb": round(total_bytes / (1024 * 1024), 3),
        "maxCacheMb": DEFAULT_MAX_CACHE_MB,
        "externalKnowledgeEnabled": True,
        "externalApiEnabledByDefault": False,
        "hotRuntimeExternalApiCallsAllowed": False,
        "userAgent": DEFAULT_USER_AGENT,
        "sourceStatus": source_status,
        "files": {
            "itemIdMap": str(root / "item_id_map.json"),
            "itemNameMap": str(root / "item_name_map.json"),
            "objectKnowledge": str(root / "object_knowledge.json"),
            "npcKnowledge": str(root / "npc_knowledge.json"),
            "skillRequirements": str(root / "skill_requirements.json"),
            "locationKnowledge": str(root / "location_knowledge.json"),
            "wikiPageCache": str(root / "wiki_page_cache"),
            "mapStaticCache": str(root / "map_static_cache"),
        },
    }
