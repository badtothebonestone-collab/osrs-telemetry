import json
from pathlib import Path
from typing import Any

from label_ranges import infer_label_for_tick, load_label_ranges


DEFAULT_RULES_PATH = Path(__file__).resolve().with_name("tab_detection_rules.json")
UNKNOWN_RESULT = {
    "activeTab": "unknown",
    "source": "unknown",
    "confidence": 0.0,
    "evidence": [],
}


def load_rules(path: str | Path | None = None) -> dict:
    rules_path = Path(path).expanduser() if path else DEFAULT_RULES_PATH

    try:
        with rules_path.open("r", encoding="utf-8") as file:
            rules = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        return {
            "schemaVersion": "tab_detection.rules.v1",
            "tabs": {},
            "aliases": {},
            "_loadError": f"unable to load tab detection rules from {rules_path}: {error}",
        }

    if not isinstance(rules, dict):
        return {
            "schemaVersion": "tab_detection.rules.v1",
            "tabs": {},
            "aliases": {},
            "_loadError": f"tab detection rules are not a JSON object: {rules_path}",
        }

    if not isinstance(rules.get("tabs"), dict):
        rules["tabs"] = {}

    if not isinstance(rules.get("aliases"), dict):
        rules["aliases"] = {}

    return rules


def infer_active_tab(
    tick,
    nearby_events=None,
    rules: dict | None = None,
    labels: dict | None = None,
    manual_override: str | None = None,
) -> dict:
    rules = rules if isinstance(rules, dict) else load_rules()
    labels = labels if isinstance(labels, dict) else load_label_ranges()
    evidence = []

    load_error = rules.get("_loadError")

    if load_error:
        evidence.append({"source": "rules", "detail": load_error})

    if manual_override and str(manual_override).strip().lower() != "auto":
        tab_name = normalize_tab_name(manual_override, rules)
        return {
            "activeTab": tab_name,
            "source": "manual",
            "confidence": 1.0,
            "evidence": [{"source": "manual", "detail": f"manual override activeTab={tab_name}"}],
        }

    label_result = infer_label_for_tick(_tick_id_from_tick(tick), labels)

    if label_result is not None:
        return label_result

    widget_result = infer_from_widgets(_widgets_from_tick(tick), rules)

    if widget_result["activeTab"] != "unknown":
        return widget_result

    evidence.extend(widget_result.get("evidence", []))

    event_result = infer_from_events(nearby_events or [], rules)

    if event_result["activeTab"] != "unknown":
        return event_result

    evidence.extend(event_result.get("evidence", []))

    # Visual fallback is intentionally skipped for now; it needs calibrated tab
    # icon regions and image statistics to stay explainable and non-brittle.
    evidence.append({
        "source": "visual",
        "detail": "visual fallback not implemented; returning unknown without guessing",
    })

    return unknown_result(evidence)


def infer_from_widgets(widgets, rules: dict | None = None) -> dict:
    rules = rules if isinstance(rules, dict) else load_rules()
    widgets = widgets if isinstance(widgets, list) else []
    scores: dict[str, dict] = {}
    generic_evidence = []

    if not widgets:
        return unknown_result([{"source": "widget", "detail": "no widgets available on tick"}])

    for widget in widgets:
        if not isinstance(widget, dict):
            continue

        if widget.get("hidden") is True:
            continue

        widget_id = int_or_none(widget.get("id") or widget.get("widgetId"))
        group_id = int_or_none(widget.get("groupId") or widget.get("group"))
        child_id = int_or_none(widget.get("childId") or widget.get("child"))

        if group_id is None and widget_id is not None:
            group_id = widget_id >> 16

        if child_id is None and widget_id is not None:
            child_id = widget_id & 0xFFFF

        searchable = " ".join(
            str(widget.get(key) or "")
            for key in ("name", "text", "tooltip", "opBase", "target", "rawName", "rawText")
        ).lower()

        for tab_name, rule in tab_rules(rules).items():
            visual_name = normalize_tab_name(rule.get("visualProfileName") or tab_name, rules)
            weight = number_or_default(rule.get("confidenceWeight"), 0.75)

            for candidate in list_values(rule.get("widgetIds")):
                if widget_id is not None and int_or_none(candidate) == widget_id:
                    add_score(scores, visual_name, 0.95, {
                        "source": "widget",
                        "rule": tab_name,
                        "detail": f"widget id {widget_id} matched",
                    })

            for candidate in list_values(rule.get("widgetGroupIds")):
                if group_id is not None and int_or_none(candidate) == group_id:
                    add_score(scores, visual_name, 0.9, {
                        "source": "widget",
                        "rule": tab_name,
                        "detail": f"widget group {group_id} matched",
                    })

            for candidate in list_values(rule.get("widgetChildIds")):
                if child_id is not None and int_or_none(candidate) == child_id:
                    add_score(scores, visual_name, 0.7, {
                        "source": "widget",
                        "rule": tab_name,
                        "detail": f"widget child {child_id} matched",
                    })

            for pattern in text_patterns(rule.get("widgetNameContains")):
                if pattern in searchable:
                    add_score(scores, visual_name, weight, {
                        "source": "widget",
                        "rule": tab_name,
                        "detail": f"visible widget text/name contained '{pattern}'",
                    })

    if not scores:
        generic_evidence.append({"source": "widget", "detail": "widgets present, but no tab rules matched"})
        return unknown_result(generic_evidence)

    return best_scored_result(scores, "widget")


def infer_from_events(events, rules: dict | None = None) -> dict:
    rules = rules if isinstance(rules, dict) else load_rules()
    events = events if isinstance(events, list) else []
    scores: dict[str, dict] = {}

    if not events:
        return unknown_result([{"source": "event", "detail": "no nearby events available"}])

    for event in events:
        if not isinstance(event, dict):
            continue

        event_type = str(event.get("eventType") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        summary = str(event.get("summary") or "")
        event_text = f"{event_type} {summary} {jsonish_text(payload)}".lower()

        for tab_name, rule in tab_rules(rules).items():
            visual_name = normalize_tab_name(rule.get("visualProfileName") or tab_name, rules)

            for candidate in list_values(rule.get("widgetLoadedGroupIds")):
                if event_type == "WidgetLoaded" and int_or_none(payload.get("groupId")) == int_or_none(candidate):
                    add_score(scores, visual_name, 0.85, {
                        "source": "event",
                        "rule": tab_name,
                        "detail": f"WidgetLoaded groupId {payload.get('groupId')} matched",
                    })

            for candidate in list_values(rule.get("eventTypeHints")):
                if event_type and event_type == str(candidate):
                    add_score(scores, visual_name, 0.35, {
                        "source": "event",
                        "rule": tab_name,
                        "detail": f"eventType {event_type} matched",
                    })

            for pattern in text_patterns(rule.get("eventSummaryContains")):
                if pattern in event_text:
                    add_score(scores, visual_name, 0.55, {
                        "source": "event",
                        "rule": tab_name,
                        "detail": f"event text contained '{pattern}'",
                    })

            for pattern in text_patterns(rule.get("eventPayloadContains")):
                if pattern in event_text:
                    add_score(scores, visual_name, 0.55, {
                        "source": "event",
                        "rule": tab_name,
                        "detail": f"event payload contained '{pattern}'",
                    })

            for matcher in list_values(rule.get("varClientIntValues")):
                if not isinstance(matcher, dict) or event_type != "VarClientIntChanged":
                    continue

                if (
                    int_or_none(payload.get("index")) == int_or_none(matcher.get("index"))
                    and int_or_none(payload.get("value")) in {int_or_none(value) for value in list_values(matcher.get("values"))}
                ):
                    add_score(scores, visual_name, 0.8, {
                        "source": "event",
                        "rule": tab_name,
                        "detail": f"VarClientIntChanged index={payload.get('index')} value={payload.get('value')} matched",
                    })

    if not scores:
        return unknown_result([{"source": "event", "detail": "nearby events present, but no tab rules matched"}])

    return best_scored_result(scores, "event")


def normalize_tab_name(value, rules: dict | None = None) -> str:
    raw = str(value or "unknown").strip()

    if not raw:
        return "unknown"

    lowered = raw.lower()
    aliases = (rules or {}).get("aliases") if isinstance(rules, dict) else {}

    if isinstance(aliases, dict) and lowered in aliases:
        return str(aliases[lowered])

    return lowered.replace(" ", "_").replace("-", "_")


def tab_rules(rules: dict) -> dict:
    tabs = rules.get("tabs") if isinstance(rules, dict) else {}
    return tabs if isinstance(tabs, dict) else {}


def unknown_result(evidence=None) -> dict:
    result = dict(UNKNOWN_RESULT)
    result["evidence"] = list(evidence or [])
    return result


def add_score(scores: dict[str, dict], tab_name: str, score: float, evidence: dict) -> None:
    current = scores.setdefault(tab_name, {"score": 0.0, "evidence": []})
    current["score"] = max(float(current["score"]), min(1.0, float(score)))
    current["evidence"].append(evidence)


def best_scored_result(scores: dict[str, dict], source: str) -> dict:
    ranked = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)

    if not ranked:
        return unknown_result([{"source": source, "detail": "no scored matches"}])

    best_name, best = ranked[0]
    second_score = ranked[1][1]["score"] if len(ranked) > 1 else -1.0

    if best["score"] <= 0.0:
        return unknown_result([{"source": source, "detail": "only zero-confidence matches were found"}])

    if second_score == best["score"]:
        return unknown_result([
            {
                "source": source,
                "detail": f"ambiguous tab matches at confidence {best['score']:.2f}",
                "candidates": [name for name, data in ranked if data["score"] == best["score"]],
            }
        ])

    return {
        "activeTab": best_name,
        "source": source,
        "confidence": round(float(best["score"]), 3),
        "evidence": best["evidence"][:20],
    }


def _widgets_from_tick(tick) -> list:
    if isinstance(tick, dict) and isinstance(tick.get("widgets"), list):
        return tick["widgets"]

    return []


def _tick_id_from_tick(tick):
    if isinstance(tick, dict):
        return tick.get("tickId")

    return None


def list_values(value) -> list:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def text_patterns(value) -> list[str]:
    return [str(item).strip().lower() for item in list_values(value) if str(item).strip()]


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def number_or_default(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def jsonish_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
