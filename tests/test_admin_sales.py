# tests/test_admin_sales.py
from datetime import date

from models import SneakerSale
from extensions import db


def test_admin_can_delete_sale_record(test_client, auth, admin_user, test_app):
    with test_app.app_context():
        sale = SneakerSale(
            sold_price=150,
            sold_currency="USD",
            sold_at=date.today(),
        )
        db.session.add(sale)
        db.session.commit()
        sale_id = sale.id

    auth.login(username=admin_user.username, password='password123')
    response = test_client.post(f'/admin/sales-breakdown/delete/{sale_id}', follow_redirects=True)
    assert response.status_code == 200

    with test_app.app_context():
        assert db.session.get(SneakerSale, sale_id) is None
