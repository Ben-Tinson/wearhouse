from datetime import date

from extensions import db
from models import Release
from utils.slugs import build_product_key, build_product_slug


def test_product_detail_resolves_by_sku(test_client, auth, test_app):
    with test_app.app_context():
        release = Release(
            sku="AB-123",
            brand="Nike",
            name="Product Pair",
            model_name="Product Pair",
            release_date=date.today(),
            is_calendar_visible=True,
        )
        db.session.add(release)
        db.session.commit()
        product_key = build_product_key(release)
        product_slug = build_product_slug(release)

    response = test_client.get(f"/products/{product_key}-{product_slug}")
    assert response.status_code == 200


def test_product_detail_redirects_on_bad_slug(test_client, auth, test_app):
    with test_app.app_context():
        release = Release(
            sku="CD-456",
            brand="Nike",
            name="Slug Pair",
            model_name="Slug Pair",
            release_date=date.today(),
            is_calendar_visible=True,
        )
        db.session.add(release)
        db.session.commit()
        product_key = build_product_key(release)

    response = test_client.get(f"/products/{product_key}-wrong-slug")
    assert response.status_code in {301, 302}
