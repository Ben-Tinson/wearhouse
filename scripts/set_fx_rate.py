import argparse
import os
import sys
from datetime import datetime
from decimal import Decimal

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app
from extensions import db
from models import ExchangeRate


def set_fx_rate(base_currency: str, quote_currency: str, rate_value: str) -> None:
    app = create_app()
    with app.app_context():
        rate_decimal = Decimal(rate_value)
        existing = (
            db.session.query(ExchangeRate)
            .filter_by(base_currency=base_currency, quote_currency=quote_currency)
            .first()
        )
        if not existing:
            existing = ExchangeRate(
                base_currency=base_currency,
                quote_currency=quote_currency,
                rate=rate_decimal,
                as_of=datetime.utcnow(),
            )
            db.session.add(existing)
        else:
            existing.rate = rate_decimal
            existing.as_of = datetime.utcnow()

        db.session.commit()
        print(f"Set FX rate {base_currency}->{quote_currency} = {rate_decimal}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upsert an FX rate in the database.")
    parser.add_argument("base_currency", type=str)
    parser.add_argument("quote_currency", type=str)
    parser.add_argument("rate", type=str)
    args = parser.parse_args()

    set_fx_rate(args.base_currency.upper(), args.quote_currency.upper(), args.rate)


if __name__ == "__main__":
    main()
