import re


def slugify(text: str) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def build_my_sneaker_slug(sneaker) -> str:
    parts = [sneaker.brand or "", sneaker.model or ""]
    if getattr(sneaker, "colorway", None):
        parts.append(sneaker.colorway)
    return slugify(" ".join(part for part in parts if part).strip())


def build_product_slug(product) -> str:
    name = getattr(product, "model_name", None) or getattr(product, "name", None) or ""
    return slugify(name)


def build_product_key(product) -> str:
    sku = getattr(product, "sku", None)
    if sku:
        raw = str(sku).upper()
        return re.sub(r"[^A-Z0-9]+", "_", raw).strip("_")
    source = getattr(product, "source", None)
    source_product_id = getattr(product, "source_product_id", None)
    if source and source_product_id:
        raw = f"{source}_{source_product_id}"
        return re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    if getattr(product, "id", None) is not None:
        return f"release_{product.id}"
    return "release_unknown"
