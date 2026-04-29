from datetime import date

from extensions import db
from models import Release, ReleaseRegion, ReleasePrice, AffiliateOffer
from services.release_csv_import_service import build_release_import_preview, apply_release_csv_import
from services.release_ingestion_service import _upsert_release


CSV_HEADERS = (
    "brand,model,colorway,sku,image_url,stockx_url,goat_url,notes,description,"
    "us_release_date,us_release_time,us_timezone,us_retail_price,us_currency,us_retailer_links,"
    "uk_release_date,uk_release_time,uk_timezone,uk_retail_price,uk_currency,uk_retailer_links,"
    "eu_release_date,eu_release_time,eu_timezone,eu_retail_price,eu_currency,eu_retailer_links\n"
)


def test_preview_requires_required_fields(test_app):
    csv_text = CSV_HEADERS + ",ModelName,,SKU123,,,,,2026-05-01,,,,,,\n"
    with test_app.app_context():
        preview = build_release_import_preview(db.session, csv_text)
        assert preview["has_errors"] is True
        assert preview["rows"][0]["errors"]


def test_duplicate_detection_by_sku(test_app):
    csv_text = (
        CSV_HEADERS
        + "Nike,Air Max,,SKU123,,,,,2026-05-01,,,,,,\n"
        + "Nike,Air Max,,SKU123,,,,,2026-05-02,,,,,,\n"
    )
    with test_app.app_context():
        preview = build_release_import_preview(db.session, csv_text)
        assert preview["rows"][1]["errors"]


def test_match_computed_slug_when_release_slug_missing(test_app):
    with test_app.app_context():
        release = Release(
            name="Air Max 1",
            model_name="Air Max 1",
            brand="Nike",
            sku="DIFF-999",
            release_date=date(2026, 1, 1),
        )
        db.session.add(release)
        db.session.commit()

        csv_text = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,,,,,\n"
        preview = build_release_import_preview(db.session, csv_text)
        match = preview["rows"][0].get("match")
        assert match
        assert match["reason"] == "computed_slug"
        assert match["release_id"] == release.id


def test_apply_import_creates_regions_prices_offers_and_release_date(test_app):
    csv_text = (
        CSV_HEADERS
        + "Nike,Air Max 1,,SKU123,https://example.com/img.jpg,,,note,desc,"
          "2026-05-01,,America/New_York,200,USD,Nike|https://nike.com,"
          "2026-04-20,,,180,GBP,Foot Locker|https://footlocker.co.uk,"
          ",,,,,\n"
    )
    with test_app.app_context():
        result = apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id="batch-1",
            ingested_by_user_id=None,
            dry_run=False,
        )
        assert result["has_errors"] is False

        release = Release.query.filter_by(sku="SKU123").first()
        assert release is not None
        assert release.release_date == date(2026, 4, 20)

        regions = ReleaseRegion.query.filter_by(release_id=release.id).all()
        region_codes = {region.region for region in regions}
        assert region_codes == {"US", "UK"}

        price = ReleasePrice.query.filter_by(release_id=release.id, region="US").first()
        assert price is not None
        assert str(price.currency) == "USD"

        offer = AffiliateOffer.query.filter_by(release_id=release.id, region="US", retailer="nike").first()
        assert offer is not None


def test_blank_does_not_clear_existing_values(test_app):
    with test_app.app_context():
        release = Release(
            name="CSV Release",
            model_name="CSV Release",
            brand="Nike",
            sku="SKU999",
            image_url="https://example.com/original.jpg",
            release_date=date(2026, 1, 2),
        )
        db.session.add(release)
        db.session.commit()

        csv_text = CSV_HEADERS + "Nike,CSV Release,,SKU999,,,,,,2026-05-01,,,,,,\n"
        result = apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id="batch-2",
            ingested_by_user_id=None,
            dry_run=False,
        )
        assert result["has_errors"] is False

        refreshed = Release.query.filter_by(sku="SKU999").first()
        assert refreshed.image_url == "https://example.com/original.jpg"


def test_kicksdb_guard_preserves_csv_fields(test_app):
    with test_app.app_context():
        release = Release(
            name="CSV Name",
            model_name="CSV Name",
            brand="CSV Brand",
            sku="SKU-CSV",
            release_date=date(2026, 2, 1),
            ingestion_source="csv_admin",
        )
        db.session.add(release)
        db.session.commit()

        fields = {
            "name": "Kicks Name",
            "brand": "Kicks Brand",
            "sku": "SKU-CSV",
            "release_date": date(2026, 3, 1),
        }
        _upsert_release(db.session, fields)
        db.session.commit()

        refreshed = Release.query.filter_by(sku="SKU-CSV").first()
        assert refreshed.name == "CSV Name"
        assert refreshed.brand == "CSV Brand"
        assert refreshed.release_date == date(2026, 2, 1)


def test_retailer_links_upsert_and_dedup(test_app):
    row = [
        "Nike",
        "Air Max 1",
        "",
        "SKU123",
        "",
        "",
        "",
        "",
        "",
        "2026-05-01",
        "",
        "",
        "",
        "",
        "Nike|https://nike.com;Nike|https://nike.com",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    csv_text = CSV_HEADERS + ",".join(row) + "\n"
    with test_app.app_context():
        result = apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id="batch-retail",
            ingested_by_user_id=None,
            dry_run=False,
        )
        assert result["has_errors"] is False
        release = Release.query.filter_by(sku="SKU123").first()
        offers = AffiliateOffer.query.filter_by(release_id=release.id, region="US", retailer="nike").all()
        assert len(offers) == 1


def test_retailer_link_malformed_flags_error(test_app):
    csv_text = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,,,Nike-https://nike.com,\n"
    with test_app.app_context():
        preview = build_release_import_preview(db.session, csv_text)
        assert preview["has_errors"] is True
        assert preview["rows"][0]["errors"]


def test_retailer_link_name_length_is_validated(test_app):
    overlong_name = "R" * 51
    row = [
        "Nike",
        "Air Max 1",
        "",
        "SKU123",
        "",
        "",
        "",
        "",
        "",
        "2026-05-01",
        "",
        "",
        "",
        "",
        f"{overlong_name}|https://nike.com",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    csv_text = CSV_HEADERS + ",".join(row) + "\n"
    with test_app.app_context():
        preview = build_release_import_preview(db.session, csv_text)
        assert preview["has_errors"] is True
        assert any("retailer name" in err.lower() for err in preview["rows"][0]["errors"])


def test_model_length_is_validated(test_app):
    overlong_model = "M" * 201
    csv_text = CSV_HEADERS + f"Nike,{overlong_model},,SKU123,,,,,,2026-05-01,,,,,,\n"
    with test_app.app_context():
        preview = build_release_import_preview(db.session, csv_text)
        assert preview["has_errors"] is True
        assert any("model is too long" in err.lower() for err in preview["rows"][0]["errors"])


def test_format_guide_row_is_ignored(test_app):
    guide_row = (
        "__FORMAT_GUIDE__,Model name,SKU,https://image.url,https://stockx.url,https://goat.url,Notes,Description,"
        "YYYY-MM-DD,HH:MM,America/New_York,200,USD,Retailer Name|https://example.com; Retailer Name|https://example.com,"
        "YYYY-MM-DD,HH:MM,Europe/London,180,GBP,Retailer Name|https://example.com; Retailer Name|https://example.com,"
        "YYYY-MM-DD,HH:MM,Europe/Paris,190,EUR,Retailer Name|https://example.com; Retailer Name|https://example.com\n"
    )
    row = [
        "Nike",
        "Air Max 1",
        "",
        "SKU123",
        "",
        "",
        "",
        "",
        "",
        "2026-05-01",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    csv_text = CSV_HEADERS + guide_row + ",".join(row) + "\n"
    with test_app.app_context():
        preview = build_release_import_preview(db.session, csv_text)
        assert preview["has_errors"] is False
        assert preview["stats"]["total_rows"] == 1
        result = apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id="batch-guide",
            ingested_by_user_id=None,
            dry_run=False,
        )
        assert result["has_errors"] is False


def test_release_price_upsert_and_blank_preserves_existing(test_app):
    csv_text = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,America/New_York,200,USD,,\n"
    with test_app.app_context():
        apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id="batch-price",
            ingested_by_user_id=None,
            dry_run=False,
        )
        release = Release.query.filter_by(sku="SKU123").first()
        price = ReleasePrice.query.filter_by(release_id=release.id, region="US").first()
        assert price is not None
        assert str(price.currency) == "USD"
        assert float(price.price) == 200.0

        csv_text_blank = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,,,,,\n"
        result = apply_release_csv_import(
            db.session,
            csv_text_blank,
            ingestion_batch_id="batch-price-2",
            ingested_by_user_id=None,
            dry_run=False,
        )
        assert result["has_errors"] is False
        refreshed = ReleasePrice.query.filter_by(release_id=release.id, region="US").first()
        assert refreshed is not None
        assert float(refreshed.price) == 200.0


def test_release_price_upsert_rewrites_currency_on_same_region(test_app):
    first_csv = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,America/New_York,200,USD,,\n"
    second_csv = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,America/New_York,180,GBP,,\n"

    with test_app.app_context():
        apply_release_csv_import(
            db.session,
            first_csv,
            ingestion_batch_id="batch-price-currency-1",
            ingested_by_user_id=None,
            dry_run=False,
        )

        apply_release_csv_import(
            db.session,
            second_csv,
            ingestion_batch_id="batch-price-currency-2",
            ingested_by_user_id=None,
            dry_run=False,
        )

        release = Release.query.filter_by(sku="SKU123").first()
        prices = ReleasePrice.query.filter_by(release_id=release.id, region="US").all()

        assert len(prices) == 1
        assert str(prices[0].currency) == "GBP"
        assert float(prices[0].price) == 180.0


def test_colorway_import_and_blank_preserves_existing(test_app):
    row = [
        "Nike",
        "Air Max 1",
        "University Red/White",
        "SKU123",
        "",
        "",
        "",
        "",
        "",
        "2026-05-01",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    csv_text = CSV_HEADERS + ",".join(row) + "\n"
    with test_app.app_context():
        result = apply_release_csv_import(
            db.session,
            csv_text,
            ingestion_batch_id="batch-colorway",
            ingested_by_user_id=None,
            dry_run=False,
        )
        assert result["has_errors"] is False
        release = Release.query.filter_by(sku="SKU123").first()
        assert release is not None
        assert release.colorway == "University Red/White"

    row_blank = [
        "Nike",
        "Air Max 1",
        "",
        "SKU123",
        "",
        "",
        "",
        "",
        "",
        "2026-05-01",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    csv_text_blank = CSV_HEADERS + ",".join(row_blank) + "\n"
    result_blank = apply_release_csv_import(
        db.session,
        csv_text_blank,
        ingestion_batch_id="batch-colorway-2",
        ingested_by_user_id=None,
        dry_run=False,
    )
    assert result_blank["has_errors"] is False
    refreshed = Release.query.filter_by(sku="SKU123").first()
    assert refreshed.colorway == "University Red/White"
