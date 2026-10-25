"""Command line interface: python -m knowledge build|wipe|find|describe|path ..."""
from __future__ import annotations

import argparse
import sys

from .builder import build_graph, wipe_service
from .lookup import EntityNotFound, describe_entity, find_entities, path_between
from .store import GraphError, GraphStore, Neo4jConfig


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="knowledge", description="SAP schema graph in Neo4j")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="load data/metadata/*.json into Neo4j (idempotent)")
    b.add_argument("services", nargs="*", help="only these services (default: all saved)")
    w = sub.add_parser("wipe", help="remove one service from the graph")
    w.add_argument("service")
    f = sub.add_parser("find", help="rank entity sets for a phrase")
    f.add_argument("text")
    f.add_argument("--limit", type=int, default=10)
    d = sub.add_parser("describe", help="fields, keys and one-hop relations of an entity set")
    d.add_argument("entity")
    pa = sub.add_parser("path", help="how two entity sets connect")
    pa.add_argument("a")
    pa.add_argument("b")
    return p


def _arrow(step: dict) -> str:
    if step["kind"] == "navigation":
        via = step["via"]
        return f"--{via}-->" if step["forward"] else f"<--{via}--"
    via = f"{step['via']} ~{step['confidence']}"
    return f"--[{via}]-->" if step["forward"] else f"<--[{via}]--"


def _print_build(result: dict) -> None:
    for service, c in result["services"].items():
        print(f"{service}: {c['entities']} entity sets, {c['properties']} properties, "
              f"{c['navigations']} navigations")
    refs = result["references"]
    print(f"\n{len(refs)} cross-service REFERENCES:")
    for r in refs:
        print(f"  {r.confidence:.2f}  {r.from_entity}.{r.property} -> {r.to_entity}   ({r.reason})")


def _print_describe(e: dict) -> None:
    print(f"{e['name']}  [{e['service']}]  keys: {', '.join(e['keys'])}")
    print(f"\nFields ({len(e['fields'])}):")
    for f in e["fields"]:
        refs = "".join(f"  -> {r['entity']} ({r['confidence']})" for r in f["references"])
        print(f"  {'*' if f['is_key'] else ' '} {f['name']:<36} {f['type']:<20} {f['label'] or ''}{refs}")
    print("\nNavigates to:    " + (", ".join(f"{n['name']} -> {n['entity']}" for n in e["navigates_to"]) or "-"))
    print("Navigated from:  " + (", ".join(f"{n['entity']}.{n['name']}" for n in e["navigated_from"]) or "-"))
    print("Referenced by:   " + (", ".join(f"{r['entity']}.{r['property']} ({r['confidence']})"
                                            for r in e["referenced_by"]) or "-"))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = None
    try:
        store = GraphStore(Neo4jConfig.from_env())
        if args.command == "build":
            _print_build(build_graph(store, services=args.services or None))
        elif args.command == "wipe":
            wipe_service(store, args.service)
            print(f"Removed {args.service} from the graph")
        elif args.command == "find":
            hits = find_entities(store, args.text, args.limit)
            if not hits:
                print(f"Nothing in the graph matches '{args.text}'")
            for i, h in enumerate(hits, 1):
                print(f"{i:>2}. {h['name']:<34} {h['score']:>6}  {h['service']}  ({', '.join(h['matched'])})")
        elif args.command == "describe":
            _print_describe(describe_entity(store, args.entity))
        else:
            path = path_between(store, args.a, args.b)
            if path is None:
                print(f"No path between {args.a} and {args.b}")
                return 1
            print(f"{path['from']}")
            for s in path["steps"]:
                print(f"  {_arrow(s)} {s['to']}")
            print(f"\n{len(path['steps'])} step(s)")
    except (GraphError, EntityNotFound, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()
    return 0
