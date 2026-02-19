# tests/test_sneaker_lookup_api.py
from routes import sneakers_routes
from services.kicks_client import KicksAPIError


def test_sneaker_lookup_api_single(test_client, auth, init_database, monkeypatch):
    user, _ = init_database
    auth.login(username=user.username, password='password123')

    test_client.application.config['KICKS_API_KEY'] = 'test-key'

    def fake_lookup(*args, **kwargs):
        return {
            'status': 'ok',
            'source': 'cache',
            'sneaker': {
                'sku': 'SB200',
                'brand': 'Jordan',
                'model_name': 'Jordan 4 SB',
                'colorway': 'Pine Green',
                'image_url': 'https://example.com/sb.png'
            }
        }

    monkeypatch.setattr(sneakers_routes, 'lookup_or_fetch_sneaker', fake_lookup)

    response = test_client.get('/api/sneaker-lookup?q=Jordan+4+SB')
    assert response.status_code == 200
    data = response.get_json()
    assert data['mode'] == 'single'
    assert data['sneaker']['sku'] == 'SB200'


def test_sneaker_lookup_api_pick(test_client, auth, init_database, monkeypatch):
    user, _ = init_database
    auth.login(username=user.username, password='password123')

    test_client.application.config['KICKS_API_KEY'] = 'test-key'

    def fake_lookup(*args, **kwargs):
        return {
            'status': 'pick',
            'source': 'kicksdb',
            'candidates': [
                {'sku': 'SB201', 'brand': 'Jordan', 'model_name': 'Jordan 4 SB', 'colorway': 'Pine Green'},
                {'sku': 'SB202', 'brand': 'Jordan', 'model_name': 'Jordan 4 SB', 'colorway': 'Bred'}
            ]
        }

    monkeypatch.setattr(sneakers_routes, 'lookup_or_fetch_sneaker', fake_lookup)

    response = test_client.get('/api/sneaker-lookup?q=Jordan+4+SB&limit=1')
    assert response.status_code == 200
    data = response.get_json()
    assert data['mode'] == 'pick'
    assert len(data['candidates']) == 1


def test_sneaker_lookup_api_forbidden(test_client, auth, init_database, monkeypatch):
    user, _ = init_database
    auth.login(username=user.username, password='password123')

    test_client.application.config['KICKS_API_KEY'] = 'test-key'

    def fake_lookup(*args, **kwargs):
        raise KicksAPIError(403, "Forbidden", "Access denied")

    monkeypatch.setattr(sneakers_routes, 'lookup_or_fetch_sneaker', fake_lookup)

    response = test_client.get('/api/sneaker-lookup?q=IQ6083-067')
    assert response.status_code == 403
    data = response.get_json()
    assert 'KicksDB' in data['message']
