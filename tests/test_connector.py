"""Offline tests: all SAP responses come from tests/fixtures or inline samples."""
import json
from pathlib import Path

import pytest
import requests

from sap_connector import (
    Config, ConfigError, SapClient, SapConnectionError, SapHttpError,
    parse_metadata, query_entity_set, save_metadata,
)
from sap_connector.config import SANDBOX_BASE_URL
from sap_connector.query import clean_entity

FIXTURES = Path(__file__).parent / "fixtures"
SERVICE = "API_SALES_ORDER_SRV"


class FakeResponse:
    def __init__(self, payload=None, status=200, content=b"", url="http://x"):
        self._payload, self.status_code, self.content, self.url = payload, status, content, url
        self.text = content.decode() if isinstance(content, bytes) else str(content)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Stands in for requests.Session; records calls and replays queued responses."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.headers, self.auth = {}, None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(*responses, auth="apikey"):
    cfg = Config(base_url=SANDBOX_BASE_URL, auth=auth, api_key="secret-key", user="u", password="p")
    session = FakeSession(*responses)
    return SapClient(cfg, session), session


@pytest.fixture(scope="module")
def meta():
    return parse_metadata((FIXTURES / f"{SERVICE}.metadata.xml").read_bytes(), SERVICE)


# --- config -----------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for k in ("SAP_TARGET", "SAP_BASE_URL", "SAP_API_KEY", "SAP_USER", "SAP_PASSWORD", "SAP_AUTH"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "missing.env"


def test_sandbox_config_defaults(monkeypatch, clean_env):
    monkeypatch.setenv("SAP_API_KEY", "k")
    cfg = Config.from_env(clean_env)
    assert (cfg.base_url, cfg.auth) == (SANDBOX_BASE_URL, "apikey")


def test_system_config_uses_basic_auth(monkeypatch, clean_env):
    monkeypatch.setenv("SAP_TARGET", "system")
    monkeypatch.setenv("SAP_BASE_URL", "https://s4.example.com/sap/opu/odata/sap")
    monkeypatch.setenv("SAP_USER", "u")
    monkeypatch.setenv("SAP_PASSWORD", "p")
    cfg = Config.from_env(clean_env)
    assert cfg.auth == "basic" and cfg.base_url.endswith("/odata/sap/")


def test_missing_credentials_raise(monkeypatch, clean_env):
    with pytest.raises(ConfigError):
        Config.from_env(clean_env)
    monkeypatch.setenv("SAP_TARGET", "system")
    with pytest.raises(ConfigError):
        Config.from_env(clean_env)


def test_secrets_not_in_repr():
    cfg = Config(base_url="u", auth="apikey", api_key="secret-key", password="pw")
    assert "secret-key" not in repr(cfg) and "pw" not in repr(cfg)


# --- client -----------------------------------------------------------------

def test_apikey_header_and_basic_auth():
    _, s = make_client()
    assert s.headers["APIKey"] == "secret-key" and s.auth is None
    _, s = make_client(auth="basic")
    assert s.auth == ("u", "p") and "APIKey" not in s.headers


def test_client_only_issues_get():
    client, _ = make_client()
    assert not any(hasattr(client, m) for m in ("post", "put", "patch", "delete"))


def test_connection_error_is_friendly():
    client, _ = make_client(requests.ConnectionError("boom secret-key"))
    with pytest.raises(SapConnectionError) as exc:
        client.get("svc/Set")
    assert "secret-key" not in str(exc.value)


def test_http_errors():
    client, _ = make_client(FakeResponse(status=401), FakeResponse(
        {"error": {"message": {"value": "Resource not found"}}}, status=404))
    with pytest.raises(SapHttpError, match="authentication failed"):
        client.get("svc/Set")
    with pytest.raises(SapHttpError, match="Resource not found"):
        client.get("svc/Set")


# --- metadata ---------------------------------------------------------------

def test_entity_sets_and_keys(meta):
    sets = {e["name"]: e for e in meta["entity_sets"]}
    assert len(sets) == 22 and meta["service"] == SERVICE
    so = sets["A_SalesOrder"]
    assert so["keys"] == ["SalesOrder"]
    assert sets["A_SalesOrderItem"]["keys"] == ["SalesOrder", "SalesOrderItem"]


def test_properties_have_name_type_label(meta):
    so = next(e for e in meta["entity_sets"] if e["name"] == "A_SalesOrder")
    props = {p["name"]: p for p in so["properties"]}
    assert props["SoldToParty"]["type"] == "Edm.String"
    assert props["SoldToParty"]["label"] == "Sold-to Party"
    assert props["TotalNetAmount"]["type"] == "Edm.Decimal"


def test_navigation_targets_resolved_from_metadata(meta):
    so = next(e for e in meta["entity_sets"] if e["name"] == "A_SalesOrder")
    navs = {n["name"]: n for n in so["navigation_properties"]}
    assert navs["to_Item"]["target_entity_set"] == "A_SalesOrderItem"
    assert all(n["target_entity_set"] for e in meta["entity_sets"] for n in e["navigation_properties"])


def test_save_metadata_roundtrip(meta, tmp_path):
    path = save_metadata(meta, tmp_path)
    assert path.name == f"{SERVICE}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == meta


# --- query ------------------------------------------------------------------

def test_query_cleans_rows_and_sends_params():
    payload = json.loads((FIXTURES / "A_SalesOrder.top3.json").read_text())
    client, session = make_client(FakeResponse(payload))
    rows = query_entity_set(client, SERVICE, "A_SalesOrder", filter="SoldToParty eq '17100001'",
                            select=["SalesOrder", "SoldToParty"], top=3, orderby="SalesOrder desc")
    url, kw = session.calls[0]
    assert url.endswith("API_SALES_ORDER_SRV/A_SalesOrder")
    assert kw["params"] == {"$format": "json", "$filter": "SoldToParty eq '17100001'",
                            "$select": "SalesOrder,SoldToParty", "$top": "3", "$orderby": "SalesOrder desc"}
    assert len(rows) == 3
    assert "__metadata" not in rows[0]
    assert rows[0]["CreationDate"] == "2016-08-17T00:00:00+00:00"
    assert rows[0]["TotalNetAmount"] == "52.65"


def test_paging_follows_next_and_respects_top():
    page1 = {"d": {"results": [{"id": 1}, {"id": 2}], "__next": "http://sap/svc/Set?$skiptoken=2"}}
    page2 = {"d": {"results": [{"id": 3}, {"id": 4}]}}
    client, session = make_client(FakeResponse(page1), FakeResponse(page2))
    assert [r["id"] for r in query_entity_set(client, "svc", "Set")] == [1, 2, 3, 4]
    assert session.calls[1][0] == "http://sap/svc/Set?$skiptoken=2"
    assert session.calls[1][1]["params"] is None

    client, session = make_client(FakeResponse(page1), FakeResponse(page2))
    assert [r["id"] for r in query_entity_set(client, "svc", "Set", top=3)] == [1, 2, 3]


def test_max_pages_stops_runaway_paging():
    page = {"d": {"results": [{"id": 1}], "__next": "http://sap/next"}}
    client, session = make_client(*[FakeResponse(page) for _ in range(5)])
    assert len(query_entity_set(client, "svc", "Set", max_pages=2)) == 2


def test_clean_entity_handles_navigation_values():
    row = clean_entity({
        "__metadata": {}, "Id": "1",
        "to_Item": {"__deferred": {"uri": "x"}},
        "to_Partner": {"results": [{"__metadata": {}, "P": "a"}]},
        "When": "/Date(0)/",
    })
    assert row == {"Id": "1", "to_Partner": [{"P": "a"}], "When": "1970-01-01T00:00:00+00:00"}
