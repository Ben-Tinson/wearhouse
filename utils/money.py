from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

from models import ExchangeRate

CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def format_money(amount: Optional[Any], currency: Optional[str]) -> Optional[str]:
    if amount is None or not currency:
        return None
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), "")
    value = _to_decimal(amount)
    if value is None:
        return None
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{symbol}{quantized:,.2f}"


def get_rate(db_session, from_currency: str, to_currency: str) -> Optional[ExchangeRate]:
    if not from_currency or not to_currency:
        return None
    if from_currency == to_currency:
        return None
    return (
        db_session.query(ExchangeRate)
        .filter_by(base_currency=from_currency, quote_currency=to_currency)
        .first()
    )


def _resolve_rate(db_session, from_currency: str, to_currency: str) -> Optional[Dict[str, Any]]:
    direct = get_rate(db_session, from_currency, to_currency)
    if direct and direct.rate:
        return {"rate": Decimal(str(direct.rate)), "as_of": direct.as_of, "inverted": False}

    inverse = get_rate(db_session, to_currency, from_currency)
    if inverse and inverse.rate:
        rate_value = Decimal(str(inverse.rate))
        if rate_value == 0:
            return None
        return {"rate": Decimal("1") / rate_value, "as_of": inverse.as_of, "inverted": True}

    return None


def convert_money(db_session, amount: Any, from_currency: str, to_currency: str) -> Optional[Decimal]:
    rate_info = _resolve_rate(db_session, from_currency, to_currency)
    if not rate_info:
        return None
    value = _to_decimal(amount)
    if value is None:
        return None
    converted = value * rate_info["rate"]
    return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def display_money(
    db_session,
    amount: Any,
    currency: Optional[str],
    preferred_currency: Optional[str],
) -> Dict[str, Any]:
    if amount is None or not currency:
        return {"display": None, "is_converted": False, "original": None, "label": None, "rate_as_of": None}

    original_display = format_money(amount, currency)
    if not preferred_currency or preferred_currency == currency:
        return {
            "display": original_display,
            "is_converted": False,
            "original": None,
            "label": None,
            "rate_as_of": None,
        }

    rate_info = _resolve_rate(db_session, currency, preferred_currency)
    if not rate_info:
        return {
            "display": original_display,
            "is_converted": False,
            "original": None,
            "label": None,
            "rate_as_of": None,
        }

    converted = convert_money(db_session, amount, currency, preferred_currency)
    if converted is None:
        return {
            "display": original_display,
            "is_converted": False,
            "original": None,
            "label": None,
            "rate_as_of": None,
        }

    return {
        "display": format_money(converted, preferred_currency),
        "is_converted": True,
        "original": original_display,
        "label": "Est.",
        "rate_as_of": rate_info.get("as_of"),
    }


def _to_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None
