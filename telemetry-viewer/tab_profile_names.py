import re


def canonical_tab_profile_key(value, *, default: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or default


def tab_profile_lookup(tab_profiles: dict) -> dict[str, str]:
    if not isinstance(tab_profiles, dict):
        return {}

    lookup = {}

    for name in tab_profiles.keys():
        key = canonical_tab_profile_key(name)

        if key and key not in lookup:
            lookup[key] = name

    return lookup


def resolve_tab_profile_key(tab_profiles: dict, requested) -> str | None:
    key = canonical_tab_profile_key(requested)

    if key == "unknown":
        return None

    return tab_profile_lookup(tab_profiles).get(key)
