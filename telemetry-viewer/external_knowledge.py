from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import external_knowledge_cache as cache


QUERY_SCHEMA = "external_knowledge_query.v1"
STATUS_SCHEMA = "external_knowledge_status.v1"
PROBE_SCHEMA = "task_probe_report.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return cache.normalize_key(value)


def _response(schema: str, data: Any, *, started: float, source: str = "external_knowledge_cache", status: str = "PASS", warnings: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": schema,
        "status": status,
        "generatedAtUtc": cache.utc_now(),
        "source": source,
        "data": data,
        "warnings": warnings or [],
        "capHit": False,
        "truncated": False,
        "performanceStats": {
            "queryTimeMs": round((time.perf_counter() - started) * 1000.0, 3),
            "objectCount": len(data) if isinstance(data, list) else _dict(data).get("count"),
            "sourceAgeMs": None,
        },
    }
    payload["performanceStats"]["responseBytes"] = len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))
    return payload


def ensure_cache(root: Path | None = None) -> Path:
    return cache.ensure_cache(root)


def source_inventory(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    payload = cache.read_json(root / "external_knowledge_sources.json", None)
    if not isinstance(payload, dict):
        payload = cache.default_source_inventory(root)
        cache.write_json(root / "external_knowledge_sources.json", payload)
    status_payload = cache.read_json(root / "source_status.json", {}) or {}
    last_refresh = _dict(status_payload.get("lastRefresh"))
    last_error = _dict(status_payload.get("lastError"))
    for source in _list(payload.get("sources")):
        if isinstance(source, dict):
            source["lastRefresh"] = last_refresh.get(source.get("sourceId"))
            source["lastError"] = last_error.get(source.get("sourceId"))
    return payload


def knowledge_status(root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    root = ensure_cache(root)
    cache_status = cache.cache_status(root)
    inventory = source_inventory(root)
    data = {
        "externalKnowledgeEnabled": True,
        "externalApiEnabledByDefault": False,
        "hotRuntimeExternalApiCallsAllowed": False,
        "cacheFirst": True,
        "explicitRefreshOnly": True,
        "cachePath": str(root),
        "cacheSizeMb": cache_status.get("cacheSizeMb"),
        "maxCacheMb": cache_status.get("maxCacheMb"),
        "userAgentRequired": True,
        "userAgent": cache.DEFAULT_USER_AGENT,
        "rateLimitPolicy": "cache-first, serial explicit refresh, no executor hot-loop requests",
        "sourceCount": len(_list(inventory.get("sources"))),
        "externalSourcesHealthy": cache_status.get("status") == "PASS",
        "externalApiDisabledReason": "External APIs are advisory and only used by explicit refresh/search commands.",
        "externalRateLimitBackoff": "serial_requests_with_error_backoff",
        "sourceInventory": inventory,
    }
    return _response(STATUS_SCHEMA, data, started=started, status=cache_status.get("status") or "PASS")


def _item_id_map(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    return cache.read_json(root / "item_id_map.json", {"items": {}}) or {"items": {}}


def _item_name_map(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    payload = cache.read_json(root / "item_name_map.json", None)
    if not isinstance(payload, dict):
        payload = cache.refresh_name_maps(root)
    return payload


def _object_map(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    return cache.read_json(root / "object_knowledge.json", {"objects": {}}) or {"objects": {}}


def _npc_map(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    return cache.read_json(root / "npc_knowledge.json", {"npcs": {}}) or {"npcs": {}}


def _skill_map(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    return cache.read_json(root / "skill_requirements.json", {"requirements": {}}) or {"requirements": {}}


def _location_map(root: Path | None = None) -> dict[str, Any]:
    root = ensure_cache(root)
    return cache.read_json(root / "location_knowledge.json", {"locations": {}}) or {"locations": {}}


def lookup_item_id(item_id: int | str, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    item = _dict(_item_id_map(root).get("items")).get(str(item_id))
    data = {
        "itemId": int(item_id) if str(item_id).isdigit() else item_id,
        "found": bool(item),
        "item": item,
        "resolverOrder": ["local_static_library", "local_external_cache", "explicit_external_refresh"],
        "cacheMisses": [] if item else [{"kind": "item", "id": item_id}],
        "suggestedExternalRefresh": None if item else "python telemetry-viewer\\context_service.py --external-refresh-item-map",
    }
    return _response("external_item_lookup.v1", data, started=started, status="PASS" if item else "WARN", warnings=[] if item else ["cache_miss"])


def search_item(name: str, *, root: Path | None = None, limit: int = 10) -> dict[str, Any]:
    started = time.perf_counter()
    needle = _norm(name)
    by_name = _dict(_item_name_map(root).get("itemsByName"))
    matches = []
    seen_ids = set()
    for key, item in by_name.items():
        if not (needle in key or key in needle):
            continue
        item_id = _dict(item).get("itemId")
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        matches.append(item)
        if len(matches) >= max(0, min(50, int(limit))):
            break
    data = {"query": name, "items": matches, "count": len(matches), "cacheMisses": [] if matches else [{"kind": "itemName", "name": name}]}
    return _response("external_item_search.v1", data, started=started, status="PASS" if matches else "WARN", warnings=[] if matches else ["cache_miss"])


def lookup_object(name: str, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    objects = _dict(_object_map(root).get("objects"))
    key = _norm(name)
    item = objects.get(key)
    if item is None:
        item = next((value for obj_key, value in objects.items() if key in obj_key or obj_key in key), None)
    data = {
        "query": name,
        "found": bool(item),
        "object": item,
        "cacheMisses": [] if item else [{"kind": "object", "name": name}],
        "suggestedExternalRefresh": None if item else f"python telemetry-viewer\\context_service.py --external-search-wiki \"{name}\"",
    }
    return _response("external_object_lookup.v1", data, started=started, status="PASS" if item else "WARN", warnings=[] if item else ["cache_miss"])


def lookup_npc(name: str, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    npcs = _dict(_npc_map(root).get("npcs"))
    key = _norm(name)
    item = npcs.get(key)
    data = {
        "query": name,
        "found": bool(item),
        "npc": item,
        "cacheMisses": [] if item else [{"kind": "npc", "name": name}],
        "suggestedExternalRefresh": None if item else f"python telemetry-viewer\\context_service.py --external-search-wiki \"{name}\"",
    }
    return _response("external_npc_lookup.v1", data, started=started, status="PASS" if item else "WARN", warnings=[] if item else ["cache_miss"])


def get_skill_requirement(name: str, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    requirements = _dict(_skill_map(root).get("requirements"))
    key = _norm(name)
    item = requirements.get(key)
    if item is None:
        item = next((value for req_key, value in requirements.items() if key in req_key or req_key in key), None)
    data = {
        "query": name,
        "found": bool(item),
        "requirement": item,
        "cacheMisses": [] if item else [{"kind": "skillRequirement", "name": name}],
    }
    return _response("external_skill_requirement_lookup.v1", data, started=started, status="PASS" if item else "WARN", warnings=[] if item else ["cache_miss"])


def lookup_area(name: str, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    locations = _dict(_location_map(root).get("locations"))
    key = _norm(name)
    item = locations.get(key)
    if item is None:
        item = next((value for loc_key, value in locations.items() if key in loc_key or loc_key in key), None)
    data = {"query": name, "found": bool(item), "area": item, "cacheMisses": [] if item else [{"kind": "area", "name": name}]}
    return _response("external_area_lookup.v1", data, started=started, status="PASS" if item else "WARN", warnings=[] if item else ["cache_miss"])


def lookup_area_by_coord(x: int | float, y: int | float, plane: int = 0, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    matches = []
    for area in _dict(_location_map(root).get("locations")).values():
        if not isinstance(area, dict):
            continue
        bounds = _dict(area.get("areaBounds"))
        planes = bounds.get("planes") or []
        if planes and int(plane) not in [int(value) for value in planes]:
            continue
        if bounds and float(bounds.get("minX", -999999)) <= float(x) <= float(bounds.get("maxX", 999999)) and float(bounds.get("minY", -999999)) <= float(y) <= float(bounds.get("maxY", 999999)):
            matches.append(area)
    data = {"query": {"worldX": x, "worldY": y, "plane": plane}, "areas": matches, "count": len(matches), "advisoryOnly": True}
    return _response("external_area_by_coord_lookup.v1", data, started=started, status="PASS" if matches else "WARN", warnings=[] if matches else ["cache_miss"])


def route_prior_between(current_area: str, service_area: str, *, root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    current = lookup_area(current_area, root=root).get("data", {}).get("area")
    service = lookup_area(service_area, root=root).get("data", {}).get("area")
    data = {
        "currentArea": current_area,
        "serviceArea": service_area,
        "currentAreaFact": current,
        "serviceAreaFact": service,
        "advisoryOnly": True,
        "liveVerificationRequired": True,
        "routePrior": {
            "summary": f"Use project service_routes plus live WorldModel route/service object censuses between {current_area} and {service_area}.",
            "staticOnly": True,
        } if current or service else None,
    }
    return _response("external_route_prior_lookup.v1", data, started=started, status="PASS" if current or service else "WARN")


def search_wiki(query: str, *, allow_refresh: bool = False, root: Path | None = None, limit: int = 5) -> dict[str, Any]:
    started = time.perf_counter()
    root = ensure_cache(root)
    slug = _norm(query).replace(" ", "_")[:80] or "query"
    cached = cache.read_json(root / "wiki_page_cache" / f"search_{slug}.json", None)
    if isinstance(cached, dict):
        return _response("external_wiki_search.v1", {"query": query, "results": cached.get("results", []), "count": cached.get("count"), "cacheHit": True}, started=started)
    if not allow_refresh:
        return _response(
            "external_wiki_search.v1",
            {
                "query": query,
                "results": [],
                "count": 0,
                "cacheHit": False,
                "externalApiCalled": False,
                "suggestedExternalRefresh": f"python telemetry-viewer\\context_service.py --external-search-wiki \"{query}\" --external-refresh",
            },
            started=started,
            status="WARN",
            warnings=["cache_miss", "external_refresh_not_allowed"],
        )
    try:
        from external_sources import osrs_wiki

        refreshed = osrs_wiki.search_pages(query, cache_root=root, limit=limit)
        return _response("external_wiki_search.v1", {"query": query, "results": refreshed.get("results", []), "count": refreshed.get("count"), "cacheHit": False, "externalApiCalled": True}, started=started)
    except Exception as error:  # noqa: BLE001
        status = cache.read_json(root / "source_status.json", {}) or {}
        status.setdefault("lastError", {})["osrs_wiki_mediawiki"] = f"{type(error).__name__}: {error}"
        cache.write_json(root / "source_status.json", status)
        return _response("external_wiki_search.v1", {"query": query, "results": [], "count": 0, "error": f"{type(error).__name__}: {error}"}, started=started, status="FAIL", warnings=["external_refresh_failed"])


def refresh_item_map(*, root: Path | None = None, user_agent: str = cache.DEFAULT_USER_AGENT, limit: int | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from external_sources import osrs_prices

        refreshed = osrs_prices.fetch_item_mapping(cache_root=root, user_agent=user_agent, limit=limit)
        return _response("external_item_map_refresh.v1", {"cachePath": str(ensure_cache(root)), "count": refreshed.get("count"), "sourceUrl": refreshed.get("sourceUrl")}, started=started)
    except Exception as error:  # noqa: BLE001
        root_path = ensure_cache(root)
        status = cache.read_json(root_path / "source_status.json", {}) or {}
        status.setdefault("lastError", {})["osrs_wiki_prices"] = f"{type(error).__name__}: {error}"
        cache.write_json(root_path / "source_status.json", status)
        return _response("external_item_map_refresh.v1", {"cachePath": str(root_path), "error": f"{type(error).__name__}: {error}"}, started=started, status="FAIL", warnings=["external_refresh_failed"])


def enrich_name(name: str, *, root: Path | None = None) -> dict[str, Any]:
    obj = lookup_object(name, root=root)
    if obj.get("status") == "PASS":
        fact = _dict(_dict(obj.get("data")).get("object"))
        return {"externalKnowledgeAvailable": True, "externalKnowledgeSource": _dict(fact.get("provenance")).get("source"), **fact}
    item = search_item(name, root=root, limit=1)
    if item.get("status") == "PASS" and _list(_dict(item.get("data")).get("items")):
        fact = _list(_dict(item.get("data")).get("items"))[0]
        return {"externalKnowledgeAvailable": True, "externalKnowledgeSource": _dict(fact.get("provenance")).get("source"), **fact}
    req = get_skill_requirement(name, root=root)
    if req.get("status") == "PASS":
        fact = _dict(_dict(req.get("data")).get("requirement"))
        return {"externalKnowledgeAvailable": True, "externalKnowledgeSource": _dict(fact.get("provenance")).get("source"), **fact}
    return {"externalKnowledgeAvailable": False, "cacheMisses": [{"name": name}], "suggestedExternalRefresh": f"python telemetry-viewer\\context_service.py --external-search-wiki \"{name}\" --external-refresh"}


def resolve_unknown(kind: str, value: Any, *, allow_refresh: bool = False, root: Path | None = None) -> dict[str, Any]:
    if kind == "item_id":
        return lookup_item_id(value, root=root)
    if kind == "item":
        return search_item(str(value), root=root)
    if kind == "object":
        return lookup_object(str(value), root=root)
    if kind == "npc":
        return lookup_npc(str(value), root=root)
    if kind == "area":
        return lookup_area(str(value), root=root)
    return search_wiki(str(value), allow_refresh=allow_refresh, root=root)
