from typing import Optional, Set


def normalize_sku(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().upper().replace(" ", "-")
    cleaned = "".join(ch for ch in normalized if ch.isalnum() or ch == "-")
    return cleaned or None


def sku_variants(value: Optional[str]) -> Set[str]:
    normalized = normalize_sku(value)
    if not normalized:
        return set()
    variants = {normalized}
    if "-" in normalized:
        variants.add(normalized.replace("-", " "))
    return variants
