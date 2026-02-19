import logging
import time
from typing import Any, Dict, Optional

import requests


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

    def search_stockx(self, query: str, include_traits: bool = True) -> Dict[str, Any]:
        params = {"query": query, "include_traits": str(include_traits).lower()}
        return self._request("GET", "/api/stockx/search", params=params)

    def get_stockx_product(
        self, id_or_slug: str, include_variants: bool = False, include_traits: bool = True
    ) -> Dict[str, Any]:
        params = {
            "include_variants": str(include_variants).lower(),
            "include_traits": str(include_traits).lower(),
        }
        return self._request("GET", f"/api/stockx/product/{id_or_slug}", params=params)

    def search_goat(self, query: str) -> Dict[str, Any]:
        params = {"query": query}
        return self._request("GET", "/api/goat/search", params=params)

    def get_goat_product(self, id_or_slug: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/goat/product/{id_or_slug}")

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("KICKS_API_KEY is not configured.")

        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    timeout=self.timeout_seconds,
                )
                self._log_quota_headers(response.headers)

                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                response.raise_for_status()
                return response.json()
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

    def _log_quota_headers(self, headers: Dict[str, Any]) -> None:
        current = headers.get("X-Quota-Current")
        key_type = headers.get("X-Key-Type")
        if current or key_type:
            self.logger.info("KicksDB quota: current=%s key_type=%s", current, key_type)
