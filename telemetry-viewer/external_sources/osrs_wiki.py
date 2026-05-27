from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import external_knowledge_cache as cache


API_URL = "https://oldschool.runescape.wiki/api.php"


def _request_json(url: str, *, user_agent: str, timeout: float = 10.0) -> dict[str, Any]:
    if not user_agent:
        raise ValueError("OSRS Wiki requests require a descriptive User-Agent")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}


def search_pages(query: str, *, cache_root: Path | None = None, user_agent: str = cache.DEFAULT_USER_AGENT, timeout: float = 10.0, limit: int = 5) -> dict[str, Any]:
    root = cache.ensure_cache(cache_root)
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": max(1, min(10, int(limit))),
        "format": "json",
        "formatversion": "2",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    started = time.perf_counter()
    payload = _request_json(url, user_agent=user_agent, timeout=timeout)
    results = payload.get("query", {}).get("search", []) if isinstance(payload.get("query"), dict) else []
    compact = [
        {
            "title": item.get("title"),
            "pageId": item.get("pageid"),
            "snippet": item.get("snippet"),
            "sourceUrl": f"https://oldschool.runescape.wiki/w/{urllib.parse.quote(str(item.get('title') or '').replace(' ', '_'))}",
            "provenance": cache.provenance("osrs_wiki_mediawiki", url, confidence=0.7),
        }
        for item in results
        if isinstance(item, dict)
    ]
    result = {
        "schema": "external_wiki_search_result.v1",
        "status": "PASS",
        "query": query,
        "results": compact,
        "count": len(compact),
        "sourceUrl": url,
        "performanceStats": {"queryTimeMs": round((time.perf_counter() - started) * 1000.0, 3)},
    }
    slug = cache.normalize_key(query).replace(" ", "_")[:80] or "query"
    cache.write_json(root / "wiki_page_cache" / f"search_{slug}.json", result)
    status = cache.read_json(root / "source_status.json", {}) or {}
    status.setdefault("lastRefresh", {})["osrs_wiki_mediawiki"] = cache.utc_now()
    status.setdefault("lastError", {})["osrs_wiki_mediawiki"] = None
    cache.write_json(root / "source_status.json", status)
    return result
