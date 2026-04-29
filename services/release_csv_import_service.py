import csv
import io
import logging
import re
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import or_

from models import Release, ReleaseRegion, ReleasePrice, AffiliateOffer
from services.release_ingestion_service import parse_release_date
from utils.sku import normalize_sku, sku_variants
from utils.slugs import build_product_slug, slugify

logger = logging.getLogger(__name__)

REGION_PREFIXES = {
    "US": "us",
    "UK": "uk",
    "EU": "eu",
}

VALID_CURRENCIES = {"GBP", "USD", "EUR"}
MAX_LENGTHS = {
    "brand": 100,
    "model_name": 200,
    "colorway": 200,
    "sku": 50,
    "release_slug": 255,
    "image_url": 500,
    "affiliate_url": 1024,
    "retailer": 50,
}

RELEASE_CSV_HEADERS = [
    "brand",
    "model",
    "colorway",
    "sku",
    "image_url",
    "stockx_url",
    "goat_url",
    "notes",
    "description",
    "us_release_date",
    "us_release_time",
    "us_timezone",
    "us_retail_price",
    "us_currency",
    "us_retailer_links",
    "uk_release_date",
    "uk_release_time",
    "uk_timezone",
    "uk_retail_price",
    "uk_currency",
    "uk_retailer_links",
    "eu_release_date",
    "eu_release_time",
    "eu_timezone",
    "eu_retail_price",
    "eu_currency",
    "eu_retailer_links",
]

REQUIRED_HEADERS = set(RELEASE_CSV_HEADERS)


try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


def build_release_import_preview(db_session: Session, csv_text: str) -> Dict[str, Any]:
    rows, header_errors = _parse_csv_rows(csv_text)
    results = []
    for row_number, raw_row in rows:
        results.append(_validate_and_normalize_row(raw_row, row_number))

    _detect_duplicate_rows(results)
    _apply_matching(db_session, results)

    error_rows = [row for row in results if row["errors"]]
    warning_rows = [row for row in results if row["warnings"]]

    return {
        "rows": results,
        "errors": header_errors,
        "warnings": [],
        "stats": {
            "total_rows": len(results),
            "error_rows": len(error_rows),
            "warning_rows": len(warning_rows),
            "valid_rows": len(results) - len(error_rows),
        },
        "has_errors": bool(header_errors) or bool(error_rows),
    }


def apply_release_csv_import(
    db_session: Session,
    csv_text: str,
    ingestion_batch_id: str,
    ingested_by_user_id: Optional[int] = None,
    dry_run: bool = True,
    skip_existing: bool = False,
) -> Dict[str, Any]:
    preview = build_release_import_preview(db_session, csv_text)
    if preview["has_errors"] or dry_run:
        preview["dry_run"] = True
        return preview

    applied = {
        "created": 0,
        "updated": 0,
    }

    try:
        for row in preview["rows"]:
            if row["errors"]:
                continue
            if skip_existing and row.get("match"):
                continue
            release, created = _upsert_release_from_row(
                db_session,
                row,
                ingestion_batch_id,
                ingested_by_user_id,
            )
            if created:
                applied["created"] += 1
            else:
                applied["updated"] += 1
        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        logger.exception("CSV import failed: %s", exc)
        raise

    preview["dry_run"] = False
    preview["applied"] = applied
    return preview


def _parse_csv_rows(csv_text: str) -> Tuple[List[Tuple[int, Dict[str, str]]], List[str]]:
    if not csv_text:
        return [], ["CSV content is empty."]

    handle = io.StringIO(csv_text)
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        return [], ["CSV header row is missing."]

    headers = [header.strip().lower() for header in reader.fieldnames]
    header_errors = []
    missing = sorted(REQUIRED_HEADERS.difference(headers))
    if missing:
        header_errors.append(f"Missing required headers: {', '.join(missing)}")

    rows = []
    for index, row in enumerate(reader, start=2):
        normalized_row = {
            (key.strip().lower() if key else ""): (value if value is not None else "")
            for key, value in row.items()
        }
        marker = _clean_value(normalized_row.get("brand") or "")
        if marker and marker.upper() == "__FORMAT_GUIDE__":
            continue
        rows.append((index, normalized_row))

    return rows, header_errors


def _validate_and_normalize_row(raw_row: Dict[str, str], row_number: int) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    brand = _clean_value(raw_row.get("brand"))
    model = _clean_value(raw_row.get("model"))
    sku_raw = _clean_value(raw_row.get("sku"))
    sku_normalized = normalize_sku(sku_raw) if sku_raw else None

    if not brand:
        errors.append("Brand is required.")
    if not model:
        errors.append("Model is required.")
    if not sku_raw:
        errors.append("SKU is required.")

    image_url = _validate_url(raw_row.get("image_url"), errors, "Image URL", max_length=MAX_LENGTHS["image_url"])
    stockx_url = _validate_url(raw_row.get("stockx_url"), errors, "StockX URL", max_length=MAX_LENGTHS["affiliate_url"])
    goat_url = _validate_url(raw_row.get("goat_url"), errors, "GOAT URL", max_length=MAX_LENGTHS["affiliate_url"])

    notes = _clean_value(raw_row.get("notes"))
    description = _clean_value(raw_row.get("description"))

    regions: Dict[str, Dict[str, Any]] = {}
    has_any_release_date = False

    for region, prefix in REGION_PREFIXES.items():
        region_block, region_errors, region_warnings = _parse_region_block(raw_row, region, prefix)
        errors.extend(region_errors)
        warnings.extend(region_warnings)
        regions[region] = region_block
        if region_block.get("release_date"):
            has_any_release_date = True

    if not has_any_release_date:
        errors.append("At least one regional release date is required (US/UK/EU).")

    release_slug = slugify(model or "") if model else None
    _validate_max_length(brand, MAX_LENGTHS["brand"], "Brand", errors)
    _validate_max_length(model, MAX_LENGTHS["model_name"], "Model", errors)
    _validate_max_length(_clean_value(raw_row.get("colorway")), MAX_LENGTHS["colorway"], "Colourway", errors)
    _validate_max_length(sku_normalized or sku_raw, MAX_LENGTHS["sku"], "SKU", errors)
    _validate_max_length(release_slug, MAX_LENGTHS["release_slug"], "Release slug", errors)

    normalized = {
        "brand": brand,
        "model_name": model,
        "colorway": _clean_value(raw_row.get("colorway")),
        "sku": sku_normalized or sku_raw,
        "image_url": image_url,
        "stockx_url": stockx_url,
        "goat_url": goat_url,
        "notes": notes,
        "description": description,
        "release_slug": release_slug,
        "regions": regions,
    }

    return {
        "row_number": row_number,
        "raw": raw_row,
        "normalized": normalized,
        "errors": errors,
        "warnings": warnings,
        "match": None,
    }


def _parse_region_block(raw_row: Dict[str, str], region: str, prefix: str) -> Tuple[Dict[str, Any], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    date_value = _clean_value(raw_row.get(f"{prefix}_release_date"))
    time_value = _clean_value(raw_row.get(f"{prefix}_release_time"))
    timezone_value = _clean_value(raw_row.get(f"{prefix}_timezone"))
    price_value = _clean_value(raw_row.get(f"{prefix}_retail_price"))
    currency_value = _clean_value(raw_row.get(f"{prefix}_currency"))
    links_value = _clean_value(raw_row.get(f"{prefix}_retailer_links"))

    release_date = None
    if date_value:
        release_date = parse_release_date(date_value)
        if not release_date:
            errors.append(f"{region} release date is invalid. Use YYYY-MM-DD, e.g. 2026-04-10.")

    release_time = None
    if time_value:
        release_time = _parse_time_value(time_value)
        if not release_time:
            errors.append(f"{region} release time is invalid. Use HH:MM in 24-hour format, e.g. 08:00.")

    timezone = None
    if timezone_value:
        if _is_valid_timezone(timezone_value):
            timezone = timezone_value
        else:
            warnings.append(f"{region} timezone is invalid. Use an IANA timezone such as America/New_York.")

    price = None
    if price_value:
        price = _parse_decimal_value(price_value)
        if price is None:
            errors.append(f"{region} retail price is invalid.")

    currency = None
    if currency_value:
        currency_upper = currency_value.upper()
        if currency_upper not in VALID_CURRENCIES:
            errors.append(
                f"{region} currency must be one of {', '.join(sorted(VALID_CURRENCIES))}. "
                "Do not use currency symbols like $ or £."
            )
        else:
            currency = currency_upper

    if price is not None and not currency:
        errors.append(f"{region} currency is required when a retail price is provided.")
    if currency and price is None and price_value is None:
        warnings.append(f"{region} currency provided without retail price.")

    retailer_links: List[Dict[str, str]] = []
    if links_value:
        parsed_links, link_errors = _parse_retailer_links(links_value, region)
        retailer_links = parsed_links
        errors.extend(link_errors)

    return {
        "release_date": release_date,
        "release_time": release_time,
        "timezone": timezone,
        "retail_price": price,
        "currency": currency,
        "retailer_links": retailer_links,
    }, errors, warnings


def _parse_retailer_links(value: str, region: str) -> Tuple[List[Dict[str, str]], List[str]]:
    links = []
    errors = []

    entries = [entry.strip() for entry in value.split(";") if entry.strip()]
    for entry in entries:
        if "|" not in entry:
            errors.append(f"{region} retailer link entry '{entry}' is malformed. Use Retailer Name|URL.")
            continue
        retailer_name, url = [part.strip() for part in entry.split("|", 1)]
        if not retailer_name:
            errors.append(f"{region} retailer link entry '{entry}' is missing a retailer name.")
            continue
        if len(retailer_name) > MAX_LENGTHS["retailer"]:
            errors.append(
                f"{region} retailer name '{retailer_name}' is too long "
                f"(max {MAX_LENGTHS['retailer']} characters)."
            )
            continue
        if not _is_valid_url(url):
            errors.append(f"{region} retailer link URL '{url}' is invalid.")
            continue
        if len(url) > MAX_LENGTHS["affiliate_url"]:
            errors.append(
                f"{region} retailer link URL '{url}' is too long "
                f"(max {MAX_LENGTHS['affiliate_url']} characters)."
            )
            continue
        retailer_normalized = re.sub(r"\s+", " ", retailer_name).strip().lower()
        links.append({"retailer": retailer_normalized, "url": url})

    return links, errors


def _detect_duplicate_rows(results: List[Dict[str, Any]]) -> None:
    seen = {}
    for row in results:
        normalized = row["normalized"]
        sku = normalized.get("sku")
        if sku:
            key = f"sku::{normalize_sku(sku) or sku}"
        else:
            region_dates = [
                normalized["regions"][region].get("release_date")
                for region in REGION_PREFIXES.keys()
            ]
            composite = "|".join([str(value or "") for value in region_dates])
            key = f"fallback::{slugify(normalized.get('brand') or '')}::{slugify(normalized.get('model_name') or '')}::{composite}"
        if key in seen:
            row["errors"].append(f"Duplicate row detected (matches row {seen[key]}).")
        else:
            seen[key] = row["row_number"]


def _apply_matching(db_session: Session, results: List[Dict[str, Any]]) -> None:
    for row in results:
        if row["errors"]:
            continue
        normalized = row["normalized"]
        release, reason = _match_release(
            db_session,
            normalized.get("sku"),
            normalized.get("release_slug"),
            normalized.get("brand"),
            normalized.get("model_name"),
        )
        if release:
            row["match"] = {
                "release_id": release.id,
                "reason": reason,
            }


def _match_release(
    db_session: Session,
    sku: Optional[str],
    release_slug: Optional[str],
    brand: Optional[str],
    model: Optional[str],
) -> Tuple[Optional[Release], Optional[str]]:
    if sku:
        normalized = normalize_sku(sku) or sku
        variants = sku_variants(normalized) or {normalized}
        sku_filters = [Release.sku.ilike(value) for value in variants if value]
        release = db_session.query(Release).filter(or_(*sku_filters)).first()
        if release:
            return release, "sku"

    if release_slug:
        release = db_session.query(Release).filter(Release.release_slug == release_slug).first()
        if release:
            return release, "release_slug"

    if model:
        candidates = db_session.query(Release).filter(Release.release_slug.is_(None))
        if brand:
            candidates = candidates.filter(Release.brand.ilike(brand))
        if model:
            candidates = candidates.filter((Release.model_name.ilike(model)) | (Release.name.ilike(model)))
        candidates = candidates.all()
        for candidate in candidates:
            if build_product_slug(candidate) == slugify(model):
                return candidate, "computed_slug"

        if brand or model:
            fallback_candidates = (
                db_session.query(Release)
                .filter(Release.release_slug.is_(None))
                .all()
            )
            for candidate in fallback_candidates:
                if build_product_slug(candidate) == slugify(model):
                    return candidate, "computed_slug"

    return None, None


def _upsert_release_from_row(
    db_session: Session,
    row: Dict[str, Any],
    ingestion_batch_id: str,
    ingested_by_user_id: Optional[int],
) -> Tuple[Release, bool]:
    normalized = row["normalized"]
    match = row.get("match") or {}
    earliest_from_row = _earliest_region_date_from_row(normalized.get("regions") or {})
    if not earliest_from_row:
        raise ValueError(f"Row {row['row_number']} has no valid regional release dates.")

    release = None
    if match.get("release_id"):
        release = db_session.get(Release, match["release_id"])

    created = False
    if not release:
        release = Release(
            name=normalized.get("model_name") or "Unknown",
            brand=normalized.get("brand"),
            model_name=normalized.get("model_name"),
            sku=normalized.get("sku"),
            release_slug=normalized.get("release_slug"),
            release_date=earliest_from_row,
        )
        created = True
        db_session.add(release)
        db_session.flush()

    _set_if_present(release, "brand", normalized.get("brand"))
    _set_if_present(release, "model_name", normalized.get("model_name"))
    _set_if_present(release, "colorway", normalized.get("colorway"))
    _set_if_present(release, "sku", normalized.get("sku"))
    _set_if_present(release, "image_url", normalized.get("image_url"))
    _set_if_present(release, "description", normalized.get("description"))
    _set_if_present(release, "notes", normalized.get("notes"))
    if normalized.get("release_slug") and not release.release_slug:
        release.release_slug = normalized.get("release_slug")

    if release.name in (None, "", "Unknown") and normalized.get("model_name"):
        release.name = normalized.get("model_name")

    release.ingestion_source = "csv_admin"
    release.ingestion_batch_id = ingestion_batch_id
    release.ingested_at = datetime.utcnow()
    if ingested_by_user_id:
        release.ingested_by_user_id = ingested_by_user_id

    regions = normalized.get("regions") or {}
    for region, block in regions.items():
        if not block.get("release_date"):
            continue
        _upsert_release_region(db_session, release, region, block)
        _upsert_release_price(db_session, release, region, block)
        _upsert_retailer_links(db_session, release, region, block)

    if normalized.get("stockx_url"):
        _upsert_affiliate_offer(db_session, release, "stockx", None, normalized.get("stockx_url"), "aftermarket")
    if normalized.get("goat_url"):
        _upsert_affiliate_offer(db_session, release, "goat", None, normalized.get("goat_url"), "aftermarket")

    earliest_date = _earliest_region_date(db_session, release)
    if earliest_date:
        release.release_date = earliest_date

    db_session.add(release)
    return release, created


def _upsert_release_region(db_session: Session, release: Release, region: str, block: Dict[str, Any]) -> None:
    region_row = (
        db_session.query(ReleaseRegion)
        .filter_by(release_id=release.id, region=region)
        .first()
    )
    if not region_row:
        region_row = ReleaseRegion(release_id=release.id, region=region, release_date=block["release_date"])
        db_session.add(region_row)

    _set_if_present(region_row, "release_date", block.get("release_date"))
    _set_if_present(region_row, "release_time", block.get("release_time"))
    _set_if_present(region_row, "timezone", block.get("timezone"))


def _upsert_release_price(db_session: Session, release: Release, region: str, block: Dict[str, Any]) -> None:
    price = block.get("retail_price")
    currency = block.get("currency")
    if price is None or not currency:
        return

    existing = (
        db_session.query(ReleasePrice)
        .filter_by(release_id=release.id, region=region)
        .first()
    )
    if existing:
        existing.currency = currency
        existing.price = price
        return

    db_session.add(
        ReleasePrice(
            release_id=release.id,
            region=region,
            currency=currency,
            price=price,
        )
    )


def _upsert_retailer_links(db_session: Session, release: Release, region: str, block: Dict[str, Any]) -> None:
    links = block.get("retailer_links") or []
    for link in links:
        _upsert_affiliate_offer(db_session, release, link["retailer"], region, link["url"], "retailer")


def _upsert_affiliate_offer(
    db_session: Session,
    release: Release,
    retailer: str,
    region: Optional[str],
    base_url: str,
    offer_type: str,
) -> None:
    if not retailer or not base_url:
        return
    offer = (
        db_session.query(AffiliateOffer)
        .filter_by(release_id=release.id, retailer=retailer, region=region)
        .first()
    )
    if not offer:
        offer = AffiliateOffer(
            release_id=release.id,
            retailer=retailer,
            region=region,
            base_url=base_url,
            offer_type=offer_type,
            is_active=True,
        )
        db_session.add(offer)
    else:
        offer.base_url = base_url
        if not offer.offer_type:
            offer.offer_type = offer_type


def _earliest_region_date(db_session: Session, release: Release) -> Optional[datetime.date]:
    dates = (
        db_session.query(ReleaseRegion.release_date)
        .filter(ReleaseRegion.release_id == release.id)
        .filter(ReleaseRegion.release_date.isnot(None))
        .all()
    )
    values = [row[0] for row in dates if row and row[0] is not None]
    return min(values) if values else None


def _earliest_region_date_from_row(regions: Dict[str, Dict[str, Any]]) -> Optional[datetime.date]:
    dates = [
        block.get("release_date")
        for block in regions.values()
        if block.get("release_date") is not None
    ]
    return min(dates) if dates else None


def _set_if_present(model: Any, field: str, value: Any) -> None:
    if value is None:
        return
    setattr(model, field, value)


def _clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def _parse_time_value(value: str) -> Optional[time]:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _parse_decimal_value(value: str) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _validate_url(value: Optional[str], errors: List[str], label: str, max_length: Optional[int] = None) -> Optional[str]:
    cleaned = _clean_value(value)
    if not cleaned:
        return None
    if not _is_valid_url(cleaned):
        errors.append(f"{label} is invalid.")
        return None
    if max_length is not None and len(cleaned) > max_length:
        errors.append(f"{label} is too long (max {max_length} characters).")
        return None
    return cleaned


def _is_valid_url(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(re.match(r"^https?://", value.strip()))


def _is_valid_timezone(value: str) -> bool:
    if not value:
        return False
    if ZoneInfo is None:
        return False
    try:
        ZoneInfo(value)
        return True
    except Exception:
        return False


def _validate_max_length(value: Optional[str], max_length: int, label: str, errors: List[str]) -> None:
    if value is None:
        return
    if len(value) > max_length:
        errors.append(f"{label} is too long (max {max_length} characters).")
