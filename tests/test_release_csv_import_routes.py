import io
from datetime import date

from extensions import db
from models import Release

CSV_HEADERS = (
    "brand,model,colorway,sku,image_url,stockx_url,goat_url,notes,description,"
    "us_release_date,us_release_time,us_timezone,us_retail_price,us_currency,us_retailer_links,"
    "uk_release_date,uk_release_time,uk_timezone,uk_retail_price,uk_currency,uk_retailer_links,"
    "eu_release_date,eu_release_time,eu_timezone,eu_retail_price,eu_currency,eu_retailer_links\n"
)


def test_release_import_requires_admin(test_client, auth, init_database):
    user, _ = init_database
    auth.login(username=user.username, password='password123')
    response = test_client.get('/admin/release-import')
    assert response.status_code == 403


def test_release_import_preview_admin(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password='password123')
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
    csv_text = CSV_HEADERS + ",".join(row) + "\n"
    data = {
        'csv_file': (io.BytesIO(csv_text.encode('utf-8')), 'releases.csv'),
    }
    response = test_client.post('/admin/release-import', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b"Preview Summary" in response.data
    assert b"Fix the errors below before importing." not in response.data


def test_release_import_confirm_flow(test_client, auth, admin_user, test_app):
    auth.login(username=admin_user.username, password='password123')
    csv_text = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2026-05-01,,,,,,\n"
    response = test_client.post(
        '/admin/release-import/confirm',
        data={'csv_text': csv_text, 'skip_existing': 'n'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        release = Release.query.filter_by(sku='SKU123').first()
        assert release is not None
        assert release.release_date == date(2026, 5, 1)


def test_release_import_template_download(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password='password123')
    response = test_client.get('/admin/release-import/template')
    assert response.status_code == 200
    assert response.headers.get('Content-Type', '').startswith('text/csv')
    lines = response.data.decode('utf-8').splitlines()
    assert len(lines) >= 3
    header_cols = lines[0].split(',')
    guide_cols = lines[1].split(',')
    sample_cols = lines[2].split(',')
    assert header_cols[0] == "brand"
    assert guide_cols[0] == "__FORMAT_GUIDE__"
    assert header_cols[2] == "colorway"
    assert len(header_cols) == len(guide_cols) == len(sample_cols)
    assert sample_cols[1] == "Air Max 1"
    assert sample_cols[2] == "University Red/White"
    assert sample_cols[9] == "2026-04-10"


def test_release_import_invalid_csv_feedback(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password='password123')
    csv_text = CSV_HEADERS + "Nike,Air Max 1,,,,,,,,\n"
    data = {
        'csv_file': (io.BytesIO(csv_text.encode('utf-8')), 'releases.csv'),
    }
    response = test_client.post('/admin/release-import', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b"Blocking Errors" in response.data
    assert b"Fix the errors below before importing." in response.data


def test_release_import_warning_only_does_not_block(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password='password123')
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
        "08:00",
        "EST",
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
    data = {
        'csv_file': (io.BytesIO(csv_text.encode('utf-8')), 'releases.csv'),
    }
    response = test_client.post('/admin/release-import', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b"Blocking Errors" not in response.data
    assert b"Fix the errors below before importing." not in response.data
    assert b"Confirm Import" in response.data


def test_release_import_skip_existing_option(test_client, auth, admin_user, test_app):
    with test_app.app_context():
        release = Release(
            name='Existing Release',
            model_name='Existing Release',
            brand='Nike',
            sku='SKU999',
            release_date=date(2026, 1, 1),
        )
        db.session.add(release)
        db.session.commit()

    auth.login(username=admin_user.username, password='password123')
    csv_text = CSV_HEADERS + "Nike,Existing Release,,SKU999,,,,,2026-05-01,,,,,,\n"
    response = test_client.post(
        '/admin/release-import/confirm',
        data={'csv_text': csv_text, 'skip_existing': 'y'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        refreshed = Release.query.filter_by(sku='SKU999').first()
        assert refreshed.release_date == date(2026, 1, 1)


def test_release_import_confirm_revalidates(test_client, auth, admin_user, test_app):
    auth.login(username=admin_user.username, password='password123')
    csv_text = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,\n"
    response = test_client.post(
        '/admin/release-import/confirm',
        data={'csv_text': csv_text, 'skip_existing': 'n'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with test_app.app_context():
        release = Release.query.filter_by(sku='SKU123').first()
        assert release is None


def test_release_import_confirm_shows_past_dated_info_message(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password='password123')
    csv_text = CSV_HEADERS + "Nike,Air Max 1,,SKU123,,,,,,2020-01-01,,,,,,\n"
    response = test_client.post(
        '/admin/release-import/confirm',
        data={'csv_text': csv_text, 'skip_existing': 'n'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"past-dated" in response.data
    assert b"upcoming release calendar" in response.data
