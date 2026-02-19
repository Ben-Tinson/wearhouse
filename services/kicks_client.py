import logging
import time
from typing import Any, Dict, Optional

import requests


class KicksAPIError(Exception):
    def __init__(self, status_code: int, message: str, response_text: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class KicksClient:
    """
    Minimal KicksDB API client.
    Set the API key via KICKS_API_KEY in your environment or config.
    """

    def __init__(
        self,
        api_key: Optional[str],
        base_url: str = "https://api.kicks.dev",
        timeout_seconds: int = 10,
        max_retries: int = 2,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.logger = logger or logging.getLogger(__name__)
        self.session = requests.Session()
        self.request_count = 0
        self.endpoints_hit = []
        self.quota_headers = {}

    def search_stockx(self, query: str, include_traits: bool = True) -> Dict[str, Any]:
        params = {"query": query}
        if include_traits:
            params["display[traits]"] = "true"
        return self._request("GET", "/v3/stockx/products", params=params)

    def get_stockx_product(
        self,
        id_or_slug: str,
        include_variants: bool = False,
        include_traits: bool = True,
        include_market: bool = False,
        include_statistics: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if include_variants:
            params["display[variants]"] = "true"
        if include_traits:
            params["display[traits]"] = "true"
        if include_market:
            params["display[market]"] = "true"
        if include_statistics:
            params["display[statistics]"] = "true"
        return self._request("GET", f"/v3/stockx/products/{id_or_slug}", params=params)

    def get_stockx_sales_history(
        self,
        product_id: str,
        limit: int = 50,
        page: int = 1,
        variant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit, "page": page}
        if variant_id:
            params["variant_id"] = variant_id
        return self._request("GET", f"/v3/stockx/products/{product_id}/sales", params=params)

    def search_goat(self, query: str) -> Dict[str, Any]:
        params = {"query": query}
        return self._request("GET", "/v3/goat/products", params=params)

    def get_goat_product(self, id_or_slug: str) -> Dict[str, Any]:
        return self._request("GET", f"/v3/goat/products/{id_or_slug}")

    def stockx_prices(
        self,
        market: str,
        skus: Optional[list] = None,
        product_ids: Optional[list] = None,
    ) -> Dict[str, Any]:
        payload = {
            "market": market,
            "skus": skus or None,
            "product_ids": product_ids or None,
        }
        return self._request("POST", "/v3/stockx/prices", json_body=payload)

    def stockx_list(
        self,
        page: int = 1,
        per_page: int = 100,
        filters: Optional[str] = None,
        sort: str = "release_date",
        include_traits: bool = False,
    ) -> Dict[str, Any]:
        normalized_sort = self._sanitize_sort(sort, {"release_date", "rank"}, "StockX")
        params = {
            "page": page,
            "per_page": per_page,
        }
        if normalized_sort:
            params["sort"] = normalized_sort
        if filters:
            params["filters"] = filters
        if include_traits:
            params["display[traits]"] = "true"
        self.logger.info(
            "KicksDB StockX filter used: %s sort=%s page=%s per_page=%s",
            filters,
            normalized_sort,
            page,
            per_page,
        )
        return self._request("GET", "/v3/stockx/products", params=params)

    def goat_list(
        self,
        page: int = 1,
        per_page: int = 100,
        filters: Optional[str] = None,
        sort: Optional[str] = None,
        include_traits: bool = False,
    ) -> Dict[str, Any]:
        normalized_sort = self._sanitize_sort(
            sort, {"rank:asc", "rank:desc", "updated_at:asc", "updated_at:desc"}, "GOAT"
        )
        params = {
            "page": page,
            "per_page": per_page,
        }
        if normalized_sort:
            params["sort"] = normalized_sort
        if filters:
            params["filters"] = filters
        if include_traits:
            params["display[traits]"] = "true"
        self.logger.info(
            "KicksDB GOAT filter used: %s sort=%s page=%s per_page=%s",
            filters,
            normalized_sort,
            page,
            per_page,
        )
        return self._request("GET", "/v3/goat/products", params=params)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("KICKS_API_KEY is not configured.")

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                request_kwargs = {
                    "headers": headers,
                    "params": params,
                    "timeout": self.timeout_seconds,
                }
                if json_body is not None:
                    request_kwargs["json"] = json_body
                response = self.session.request(
                    method,
                    url,
                    **request_kwargs,
                )
                self.request_count += 1
                self.endpoints_hit.append(path)
                self._capture_quota_headers(response.headers)
                self._log_quota_headers(response.headers)
                self._log_request(path, response.status_code)

                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                if response.status_code >= 400:
                    self._log_error_response(response)
                    raise KicksAPIError(
                        response.status_code,
                        f"KicksDB request failed with status {response.status_code}",
                        response.text[:500],
                    )

                return response.json()
            except KicksAPIError as exc:
                last_error = exc
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break

        if last_error:
            self.logger.error("KicksDB request failed: %s", last_error)
            raise last_error
        raise RuntimeError("KicksDB request failed without an exception.")

    def _sanitize_sort(self, sort: Optional[str], allowed: set, label: str) -> Optional[str]:
        if not sort:
            return None
        if sort not in allowed:
            self.logger.warning("KicksDB %s sort rejected locally: %s", label, sort)
            return None
        return sort

    def _log_quota_headers(self, headers: Dict[str, Any]) -> None:
        current = headers.get("X-Quota-Current")
        key_type = headers.get("X-Key-Type")
        if current or key_type:
            self.logger.info("KicksDB quota: current=%s key_type=%s", current, key_type)

    def _capture_quota_headers(self, headers: Dict[str, Any]) -> None:
        self.quota_headers = {
            "current": headers.get("X-Quota-Current"),
            "key_type": headers.get("X-Key-Type"),
        }

    def _log_request(self, path: str, status_code: int) -> None:
        self.logger.info(
            "KicksDB request: path=%s status=%s quota_current=%s key_type=%s",
            path,
            status_code,
            self.quota_headers.get("current"),
            self.quota_headers.get("key_type"),
        )

    def _log_error_response(self, response: requests.Response) -> None:
        snippet = response.text[:500] if response.text else ""
        self.logger.error(
            "KicksDB error status=%s url=%s body=%s quota_current=%s key_type=%s",
            response.status_code,
            response.url,
            snippet,
            response.headers.get("X-Quota-Current"),
            response.headers.get("X-Key-Type"),
        )
