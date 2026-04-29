from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_

from models import ReleaseSalePoint, ReleaseMarketStats, SneakerDB
from utils.money import convert_money, display_money, format_money
from utils.sku import sku_variants


def find_matching_sneaker_record(release, db_session) -> Optional[SneakerDB]:
    if not release:
        return None

    source = (getattr(release, "source", None) or "").lower()
    source_product_id = getattr(release, "source_product_id", None)
    source_slug = getattr(release, "source_slug", None)

    source_filters = []
    if source == "kicksdb_stockx":
        if source_product_id:
            source_filters.append(func.lower(SneakerDB.stockx_id) == source_product_id.lower())
        if source_slug:
            source_filters.append(func.lower(SneakerDB.stockx_slug) == source_slug.lower())
    elif source == "kicksdb_goat":
        if source_product_id:
            source_filters.append(func.lower(SneakerDB.goat_id) == source_product_id.lower())
        if source_slug:
            source_filters.append(func.lower(SneakerDB.goat_slug) == source_slug.lower())

    if source_filters:
        record = db_session.query(SneakerDB).filter(or_(*source_filters)).first()
        if record:
            return record

    if getattr(release, "sku", None):
        sku_filters = [SneakerDB.sku.ilike(value) for value in sku_variants(release.sku)]
        if sku_filters:
            record = db_session.query(SneakerDB).filter(or_(*sku_filters)).first()
            if record:
                return record

    model_name = getattr(release, "model_name", None) or getattr(release, "name", None)
    if model_name:
        return (
            db_session.query(SneakerDB)
            .filter(
                or_(
                    func.lower(SneakerDB.model_name) == model_name.lower(),
                    func.lower(SneakerDB.name) == model_name.lower(),
                )
            )
            .first()
        )

    return None


def build_release_detail_extras(
    release,
    db_session,
    preferred_currency: str,
    display_data: Optional[Dict[str, Any]] = None,
    avg_resale_price: Optional[Any] = None,
    avg_resale_currency: Optional[str] = None,
    sneaker_record: Optional[SneakerDB] = None,
    market_stats: Optional[ReleaseMarketStats] = None,
) -> Dict[str, Any]:
    release_description = None
    if release:
        release_description = getattr(release, "description", None) or getattr(release, "notes", None)
    if release and sneaker_record is None:
        sneaker_record = find_matching_sneaker_record(release, db_session)
    if sneaker_record:
        release_description = release_description or sneaker_record.description or None

    market_metrics = _resolve_market_metrics(
        release,
        db_session,
        preferred_currency,
        display_data,
        avg_resale_price,
        avg_resale_currency,
        market_stats=market_stats,
    )
    average_resale_summary = _build_average_resale_summary(
        release,
        db_session,
        preferred_currency,
        avg_resale_price,
        avg_resale_currency,
        market_stats=market_stats,
    )

    return {
        "release_description": release_description,
        "market_metrics": market_metrics,
        "average_resale_summary": average_resale_summary,
    }


def _build_average_resale_summary(
    release,
    db_session,
    preferred_currency: str,
    avg_resale_price: Optional[Any],
    avg_resale_currency: Optional[str],
    market_stats: Optional[ReleaseMarketStats] = None,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"primary": None, "secondary": None}
    if not release:
        return summary

    if market_stats is None:
        market_stats = db_session.query(ReleaseMarketStats).filter_by(release_id=release.id).first()
    if market_stats:
        stats_currency = (
            market_stats.currency
            or getattr(release, "retail_currency", None)
            or preferred_currency
        )

        def build_entry(label: str, amount: Optional[Any]) -> Optional[Dict[str, Any]]:
            if amount is None:
                return None
            display = display_money(db_session, amount, stats_currency, preferred_currency)
            if not display or not display.get("display"):
                return None
            return {
                "label": label,
                "amount": amount,
                "currency": stats_currency,
                "display": display,
            }

        avg_1m = build_entry("Avg 1-Month Resale", market_stats.average_price_1m)
        avg_3m = build_entry("Avg 3-Month Resale", market_stats.average_price_3m)
        avg_1y = build_entry("Avg 1-Year Resale", market_stats.average_price_1y)

        if avg_1m:
            summary["primary"] = avg_1m
            return summary
        if avg_3m:
            summary["primary"] = avg_3m
            return summary
        if avg_1y:
            summary["primary"] = avg_1y
            return summary

    if avg_resale_price is not None and avg_resale_currency:
        display = display_money(
            db_session, avg_resale_price, avg_resale_currency, preferred_currency
        )
        if display and display.get("display"):
            summary["primary"] = {
                "label": "Average resale",
                "amount": avg_resale_price,
                "currency": avg_resale_currency,
                "display": display,
            }
    return summary


def _resolve_market_metrics(
    release,
    db_session,
    preferred_currency: str,
    display_data: Optional[Dict[str, Any]],
    avg_resale_price: Optional[Any],
    avg_resale_currency: Optional[str],
    market_stats: Optional[ReleaseMarketStats] = None,
) -> List[Dict[str, Any]]:
    metrics: List[Dict[str, Any]] = []
    if not release or not display_data:
        return metrics

    if market_stats is None:
        market_stats = db_session.query(ReleaseMarketStats).filter_by(release_id=release.id).first()
    retail_price_value = display_data.get("price")
    retail_currency = display_data.get("price_currency")

    premium_value = None
    premium_source_price = avg_resale_price
    premium_source_currency = avg_resale_currency
    if market_stats and market_stats.average_price_1y is not None:
        premium_source_price = market_stats.average_price_1y
        premium_source_currency = (
            market_stats.currency
            or getattr(release, "retail_currency", None)
            or preferred_currency
        )
    if premium_source_price is not None and premium_source_currency and retail_currency:
        if premium_source_currency == retail_currency:
            premium_value = premium_source_price
        else:
            premium_value = convert_money(
                db_session, premium_source_price, premium_source_currency, retail_currency
            )
    if premium_value is None and getattr(release, "heat_premium_ratio", None) is not None:
        try:
            premium_ratio = Decimal(str(release.heat_premium_ratio))
        except (ValueError, TypeError, InvalidOperation):
            premium_ratio = None
        if premium_ratio is not None:
            pct_value = round(float((premium_ratio - Decimal("1")) * Decimal("100")), 1)
            direction = "premium" if pct_value >= 0 else "discount"
            metrics.append(
                {"label": "Price premium (1Y)", "value": f"{pct_value:+.1f}% {direction}"}
            )
            premium_value = None

    if premium_value is not None and retail_price_value is not None and retail_currency:
        retail_decimal = Decimal(str(retail_price_value))
        if retail_decimal != 0:
            delta = Decimal(str(premium_value)) - retail_decimal
            pct = (delta / retail_decimal) * Decimal("100")
            pct_value = round(float(pct), 1)
            direction = "premium" if pct_value >= 0 else "discount"
            metrics.append(
                {"label": "Price premium (1Y)", "value": f"{pct_value:+.1f}% {direction}"}
            )

    if market_stats:
        stats_currency = (
            market_stats.currency
            or getattr(release, "retail_currency", None)
            or preferred_currency
        )
        if market_stats.volatility is not None:
            volatility_value = float(market_stats.volatility)
            if 0 < volatility_value <= 1:
                volatility_value *= 100
            elif volatility_value > 100:
                volatility_value = round(volatility_value, 2)
            metrics.append({"label": "Volatility (1Y)", "value": f"{volatility_value:.1f}%"})

        if market_stats.sales_price_range_low is not None or market_stats.sales_price_range_high is not None:
            low_display = display_money(
                db_session,
                market_stats.sales_price_range_low,
                stats_currency,
                preferred_currency,
            )
            high_display = display_money(
                db_session,
                market_stats.sales_price_range_high,
                stats_currency,
                preferred_currency,
            )
            low_value = low_display.get("display") if low_display else None
            high_value = high_display.get("display") if high_display else None
            if low_value and high_value:
                range_display = f"{low_value} – {high_value}"
            else:
                range_display = low_value or high_value
            if range_display:
                metrics.append({"label": "Sales price range (1Y)", "value": range_display})

    recent_sales_count = None
    if market_stats and market_stats.sales_volume is not None:
        recent_sales_count = int(market_stats.sales_volume)
    else:
        recent_cutoff = date.today() - timedelta(days=30)
        recent_sales_count = (
            db_session.query(func.count(ReleaseSalePoint.id))
            .filter(
                ReleaseSalePoint.release_id == release.id,
                ReleaseSalePoint.sale_at >= recent_cutoff,
            )
            .scalar()
        )
    if recent_sales_count:
        metrics.append(
            {"label": "Sales volume (last 3 months)", "value": str(recent_sales_count)}
        )

    return metrics
