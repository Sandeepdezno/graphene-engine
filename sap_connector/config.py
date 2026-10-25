"""Connector configuration, read from environment variables (and .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

SANDBOX_BASE_URL = "https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap/"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    base_url: str
    auth: str  # "apikey" or "basic"
    api_key: str = field(default="", repr=False)
    user: str = ""
    password: str = field(default="", repr=False)
    verify_ssl: bool = True
    timeout: float = 30.0
    target: str = "sandbox"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Config":
        load_dotenv(env_file or PROJECT_ROOT / ".env")
        e = os.environ
        target = e.get("SAP_TARGET", "sandbox").strip().lower()
        if target not in ("sandbox", "system"):
            raise ConfigError(f"SAP_TARGET must be 'sandbox' or 'system', got {target!r}")

        default_url = SANDBOX_BASE_URL if target == "sandbox" else ""
        base_url = e.get("SAP_BASE_URL", default_url).strip()
        if not base_url:
            raise ConfigError("SAP_BASE_URL is required when SAP_TARGET=system")
        if not base_url.endswith("/"):
            base_url += "/"

        auth = e.get("SAP_AUTH", "apikey" if target == "sandbox" else "basic").strip().lower()
        if auth not in ("apikey", "basic"):
            raise ConfigError(f"SAP_AUTH must be 'apikey' or 'basic', got {auth!r}")
        if auth == "apikey" and not e.get("SAP_API_KEY"):
            raise ConfigError("SAP_API_KEY is not set (add it to .env)")
        if auth == "basic" and not (e.get("SAP_USER") and e.get("SAP_PASSWORD")):
            raise ConfigError("SAP_USER and SAP_PASSWORD are required for basic auth")

        return cls(
            base_url=base_url,
            auth=auth,
            api_key=e.get("SAP_API_KEY", ""),
            user=e.get("SAP_USER", ""),
            password=e.get("SAP_PASSWORD", ""),
            verify_ssl=e.get("SAP_VERIFY_SSL", "true").strip().lower() not in ("0", "false", "no"),
            timeout=float(e.get("SAP_TIMEOUT", "30")),
            target=target,
        )
