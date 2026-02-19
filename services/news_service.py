import re


def slugify(value: str) -> str:
    if not value:
        return ""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value


def parse_tags(raw: str) -> list:
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",")]
    return [part for part in parts if part]


def normalise_tags(raw: str) -> str:
    tags = parse_tags(raw)
    return ", ".join(tags)
