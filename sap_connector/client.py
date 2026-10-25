"""Read-only HTTP client for SAP OData services. Only GET is ever issued."""
from __future__ import annotations

import requests

from .config import Config


class SapConnectionError(Exception):
    """SAP could not be reached (network, DNS, TLS, timeout)."""


class SapHttpError(Exception):
    """SAP answered with an HTTP error status."""

    def __init__(self, status: int, url: str, detail: str = ""):
        self.status, self.url, self.detail = status, url, detail
        super().__init__(f"SAP returned HTTP {status} for {url}" + (f": {detail}" if detail else ""))


class SapClient:
    def __init__(self, config: Config, session: requests.Session | None = None):
        self.config = config
        self._session = session or requests.Session()
        if config.auth == "apikey":
            self._session.headers["APIKey"] = config.api_key
        else:
            self._session.auth = (config.user, config.password)

    def get(self, url: str, params: dict | None = None, accept: str = "application/json") -> requests.Response:
        """GET an absolute URL or one relative to the base URL."""
        if not url.startswith("http"):
            url = self.config.base_url + url.lstrip("/")
        try:
            resp = self._session.get(
                url, params=params, headers={"Accept": accept},
                timeout=self.config.timeout, verify=self.config.verify_ssl,
            )
        except requests.RequestException as exc:
            raise SapConnectionError(
                f"Cannot reach SAP at {self.config.base_url} ({type(exc).__name__}). "
                "Check SAP_BASE_URL, network/VPN and SSL settings."
            ) from None
        if resp.status_code >= 400:
            raise SapHttpError(resp.status_code, resp.url, _error_detail(resp))
        return resp


def _error_detail(resp: requests.Response) -> str:
    if resp.status_code in (401, 403):
        return "authentication failed - check SAP_API_KEY / SAP_USER / SAP_PASSWORD"
    try:
        msg = resp.json()["error"]["message"]
        return msg["value"] if isinstance(msg, dict) else str(msg)
    except (ValueError, KeyError, TypeError):
        return resp.text[:200].strip()
