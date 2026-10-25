"""Command line interface: python -m sap_connector describe|query ..."""
from __future__ import annotations

import argparse
import json
import sys

from .client import SapClient, SapConnectionError, SapHttpError
from .config import Config, ConfigError
from .metadata import describe_service, save_metadata
from .query import query_entity_set


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sap_connector", description="Read-only SAP OData connector")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("describe", help="fetch, parse and save $metadata of a service")
    d.add_argument("service", help="e.g. API_SALES_ORDER_SRV")
    d.add_argument("--json", action="store_true", help="print the full parsed metadata as JSON")

    q = sub.add_parser("query", help="query an entity set")
    q.add_argument("service")
    q.add_argument("entity_set", help="e.g. A_SalesOrder")
    q.add_argument("--filter")
    q.add_argument("--select", help="comma-separated property names")
    q.add_argument("--top", type=int)
    q.add_argument("--orderby")
    return p


def _print_summary(meta: dict) -> None:
    print(f"Service {meta['service']}: {len(meta['entity_sets'])} entity sets")
    for es in meta["entity_sets"]:
        navs = ", ".join(n["name"] for n in es["navigation_properties"]) or "-"
        print(f"\n{es['name']}  (keys: {', '.join(es['keys'])})")
        print(f"  properties ({len(es['properties'])}):")
        for p in es["properties"]:
            print(f"    {p['name']:<40} {p['type']:<14} {p['label'] or ''}")
        print(f"  navigation: {navs}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        client = SapClient(Config.from_env())
        if args.command == "describe":
            meta = describe_service(client, args.service)
            path = save_metadata(meta)
            if args.json:
                print(json.dumps(meta, indent=2, ensure_ascii=False))
            else:
                _print_summary(meta)
            print(f"\nSaved metadata to {path}", file=sys.stderr)
        else:
            rows = query_entity_set(
                client, args.service, args.entity_set, filter=args.filter,
                select=args.select, top=args.top, orderby=args.orderby,
            )
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            print(f"\n{len(rows)} row(s)", file=sys.stderr)
    except (ConfigError, SapConnectionError, SapHttpError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
