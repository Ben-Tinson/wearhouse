from services.kicks_client import KicksClient


class DummyResponse:
    def __init__(self, url, status_code=200, payload=None, headers=None):
        self.url = url
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


def test_kicks_client_uses_filters_param_and_valid_sort():
    captured = {}

    def fake_request(method, url, headers=None, params=None, timeout=None):
        captured["params"] = params or {}
        return DummyResponse(url, payload={"results": []})

    client = KicksClient(api_key="test-key")
    client.session.request = fake_request

    client.stockx_list(page=1, per_page=100, filters='product_type = "sneakers"', sort="release_date")

    assert "filters" in captured["params"]
    assert "filter" not in captured["params"]
    assert captured["params"].get("sort") == "release_date"


def test_kicks_client_rejects_invalid_stockx_sort():
    captured = {}

    def fake_request(method, url, headers=None, params=None, timeout=None):
        captured["params"] = params or {}
        return DummyResponse(url, payload={"results": []})

    client = KicksClient(api_key="test-key")
    client.session.request = fake_request

    client.stockx_list(page=1, per_page=100, filters='product_type = "sneakers"', sort="release_date:asc")

    assert "filters" in captured["params"]
    assert "filter" not in captured["params"]
    assert "sort" not in captured["params"]


def test_kicks_client_rejects_invalid_goat_sort():
    captured = {}

    def fake_request(method, url, headers=None, params=None, timeout=None):
        captured["params"] = params or {}
        return DummyResponse(url, payload={"results": []})

    client = KicksClient(api_key="test-key")
    client.session.request = fake_request

    client.goat_list(page=1, per_page=100, filters='product_type = "sneakers"', sort="release_date:asc")

    assert "filters" in captured["params"]
    assert "filter" not in captured["params"]
    assert "sort" not in captured["params"]
