"""Query OData v2 entity sets and return clean Python dicts."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .client import SapClient

_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d+)?\)/$")
DEFAULT_MAX_PAGES = 20


def query_entity_set(
    client: SapClient,
    service: str,
    entity_set: str,
    *,
    filter: str | None = None,
    select: list[str] | str | None = None,
    top: int | None = None,
    orderby: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> list[dict]:
    """Return rows of an entity set. Follows server paging (__next) until `top` rows
    are collected, the data runs out, or `max_pages` pages were fetched."""
    params: dict[str, str] = {"$format": "json"}
    if filter:
        params["$filter"] = filter
    if select:
        params["$select"] = select if isinstance(select, str) else ",".join(select)
    if top is not None:
        params["$top"] = str(top)
    if orderby:
        params["$orderby"] = orderby

    rows: list[dict] = []
    url: str | None = f"{service}/{entity_set}"
    for _ in range(max_pages):
        payload = client.get(url, params=params).json()
        page_rows, next_url = parse_page(payload)
        rows.extend(page_rows)
        if top is not None and len(rows) >= top:
            return rows[:top]
        if not next_url:
            break
        url, params = next_url, None  # __next is a full URL that already carries the query
    return rows


def parse_page(payload: dict) -> tuple[list[dict], str | None]:
    """Parse one OData v2 JSON response into (rows, next_page_url)."""
    d = payload.get("d", payload)
    if isinstance(d, dict) and "results" in d:
        items, next_url = d["results"], d.get("__next")
    elif isinstance(d, dict):  # single entity
        items, next_url = [d], None
    else:
        items, next_url = [], None
    return [clean_entity(i) for i in items], next_url


def clean_entity(entity: dict) -> dict:
    """Drop __metadata and deferred navigation links; convert /Date()/ values to ISO strings."""
    out = {}
    for key, value in entity.items():
        if key == "__metadata":
            continue
        if isinstance(value, dict):
            if "__deferred" in value:
                continue
            if "results" in value:  # expanded to-many navigation
                out[key] = [clean_entity(v) for v in value["results"]]
                continue
            out[key] = clean_entity(value)  # expanded to-one navigation
            continue
        out[key] = _convert(value)
    return out


def _convert(value):
    if isinstance(value, str):
        m = _DATE_RE.match(value)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).isoformat()
    return value
