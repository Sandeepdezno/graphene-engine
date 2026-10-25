"""Load parsed SAP metadata (data/metadata/*.json) into Neo4j as a schema graph.

Nodes: Service, EntitySet, Property.  Relationships: HAS_ENTITY, HAS_PROPERTY,
NAVIGATES_TO {name}, and cross-service REFERENCES {confidence, reason} (Property -> EntitySet).
Every write is a MERGE on a stable id, so re-running a build never duplicates anything.
"""
from __future__ import annotations

import json
from pathlib import Path

from .references import Reference, detect_references
from .store import METADATA_DIR, GraphStore

CONSTRAINTS = [
    "CREATE CONSTRAINT service_name IF NOT EXISTS FOR (n:Service) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:EntitySet) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT property_id IF NOT EXISTS FOR (n:Property) REQUIRE n.id IS UNIQUE",
]


def load_metadata(directory: Path = METADATA_DIR, services: list[str] | None = None) -> list[dict]:
    metas = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(directory.glob("*.json"))]
    if services:
        missing = set(services) - {m["service"] for m in metas}
        if missing:
            raise FileNotFoundError(f"No saved metadata for {sorted(missing)} in {directory} "
                                    "(run 'python -m sap_connector describe <service>' first)")
        metas = [m for m in metas if m["service"] in services]
    if not metas:
        raise FileNotFoundError(f"No metadata JSON files in {directory}")
    return metas


def entity_id(service: str, entity: str) -> str:
    return f"{service}.{entity}"


def property_id(service: str, entity: str, prop: str) -> str:
    return f"{service}.{entity}.{prop}"


def to_rows(meta: dict) -> dict[str, list[dict]]:
    """Flatten one service's metadata into parameter rows for the UNWIND queries."""
    svc = meta["service"]
    entities, props, navs = [], [], []
    for es in meta["entity_sets"]:
        eid = entity_id(svc, es["name"])
        entities.append({"id": eid, "name": es["name"], "service": svc,
                         "label": es.get("label"), "entity_type": es.get("entity_type")})
        keys = set(es["keys"])
        for pos, p in enumerate(es["properties"]):
            props.append({"id": property_id(svc, es["name"], p["name"]), "entity_id": eid,
                          "name": p["name"], "type": p["type"], "label": p.get("label"),
                          "is_key": p["name"] in keys, "position": pos, "max_length": p.get("max_length")})
        for n in es["navigation_properties"]:
            if n.get("target_entity_set"):
                navs.append({"from_id": eid, "to_id": entity_id(svc, n["target_entity_set"]), "name": n["name"]})
    return {"entities": entities, "properties": props, "navigations": navs}


def ensure_constraints(store: GraphStore) -> None:
    for stmt in CONSTRAINTS:
        store.run(stmt)


def wipe_service(store: GraphStore, service: str) -> None:
    store.run(
        """
        MATCH (s:Service {name: $service})
        OPTIONAL MATCH (s)-[:HAS_ENTITY]->(e:EntitySet)
        OPTIONAL MATCH (e)-[:HAS_PROPERTY]->(p:Property)
        DETACH DELETE p, e, s
        """, service=service)


def load_service(store: GraphStore, meta: dict) -> dict[str, int]:
    rows = to_rows(meta)
    store.run("MERGE (s:Service {name: $name})", name=meta["service"])
    store.run(
        """
        UNWIND $rows AS r
        MATCH (s:Service {name: r.service})
        MERGE (e:EntitySet {id: r.id})
        SET e.name = r.name, e.service = r.service, e.label = r.label, e.entity_type = r.entity_type
        MERGE (s)-[:HAS_ENTITY]->(e)
        """, rows=rows["entities"])
    store.run(
        """
        UNWIND $rows AS r
        MATCH (e:EntitySet {id: r.entity_id})
        MERGE (p:Property {id: r.id})
        SET p.name = r.name, p.type = r.type, p.label = r.label, p.is_key = r.is_key,
            p.max_length = r.max_length, p.position = r.position, p.entity_id = r.entity_id
        MERGE (e)-[:HAS_PROPERTY]->(p)
        """, rows=rows["properties"])
    store.run(
        """
        UNWIND $rows AS r
        MATCH (a:EntitySet {id: r.from_id}), (b:EntitySet {id: r.to_id})
        MERGE (a)-[n:NAVIGATES_TO {name: r.name}]->(b)
        """, rows=rows["navigations"])
    return {k: len(v) for k, v in rows.items()}


def load_references(store: GraphStore, refs: list[Reference]) -> None:
    rows = [{"prop_id": property_id(r.from_service, r.from_entity, r.property),
             "to_id": entity_id(r.to_service, r.to_entity),
             "confidence": r.confidence, "reason": r.reason} for r in refs]
    store.run(
        """
        UNWIND $rows AS r
        MATCH (p:Property {id: r.prop_id}), (t:EntitySet {id: r.to_id})
        MERGE (p)-[x:REFERENCES]->(t)
        SET x.confidence = r.confidence, x.reason = r.reason
        """, rows=rows)


def build_graph(store: GraphStore, directory: Path = METADATA_DIR,
                services: list[str] | None = None) -> dict:
    """(Re)build the graph. Selected services are wiped and reloaded; cross-service references
    are then detected across every saved service that is in the graph."""
    selected = load_metadata(directory, services)
    ensure_constraints(store)
    counts = {}
    for meta in selected:
        wipe_service(store, meta["service"])
        counts[meta["service"]] = load_service(store, meta)

    in_graph = {r["name"] for r in store.run("MATCH (s:Service) RETURN s.name AS name")}
    universe = [m for m in load_metadata(directory) if m["service"] in in_graph]
    selected_names = {m["service"] for m in selected}
    refs = [r for r in detect_references(universe)
            if r.from_service in selected_names or r.to_service in selected_names]
    load_references(store, refs)
    return {"services": counts, "references": refs}
