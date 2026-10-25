"""Fetch and parse OData v2 $metadata (EDMX) into plain JSON-able dicts."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .client import SapClient
from .config import PROJECT_ROOT

METADATA_DIR = PROJECT_ROOT / "data" / "metadata"
SAP_NS = "http://www.sap.com/Protocols/SAPData"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str):
    return (c for c in el if _local(c.tag) == name)


def _sap(el: ET.Element, attr: str) -> str | None:
    return el.get(f"{{{SAP_NS}}}{attr}")


def _short(qualified: str | None) -> str | None:
    return qualified.rsplit(".", 1)[-1] if qualified else None


def parse_metadata(xml_text: str | bytes, service: str = "") -> dict:
    """Parse EDMX into {service, entity_sets: [...]} with keys, properties and navigations."""
    root = ET.fromstring(xml_text)
    schemas = [s for s in root.iter() if _local(s.tag) == "Schema"]

    entity_types: dict[str, dict] = {}   # qualified type name -> type info
    association_ends: dict[str, dict[str, str]] = {}  # qualified association -> {role: type}
    for schema in schemas:
        ns = schema.get("Namespace", "")
        for et in _children(schema, "EntityType"):
            max_len = lambda p: int(p.get("MaxLength")) if p.get("MaxLength", "").isdigit() else None
            entity_types[f"{ns}.{et.get('Name')}"] = {
                "name": et.get("Name"),
                "keys": [r.get("Name") for k in _children(et, "Key") for r in _children(k, "PropertyRef")],
                "properties": [
                    {"name": p.get("Name"), "type": p.get("Type"), "label": _sap(p, "label"),
                     "nullable": p.get("Nullable", "true") != "false", "max_length": max_len(p)}
                    for p in _children(et, "Property")
                ],
                "navigation_properties": [
                    {"name": n.get("Name"), "relationship": n.get("Relationship"), "to_role": n.get("ToRole")}
                    for n in _children(et, "NavigationProperty")
                ],
            }
        for assoc in _children(schema, "Association"):
            association_ends[f"{ns}.{assoc.get('Name')}"] = {
                e.get("Role"): e.get("Type") for e in _children(assoc, "End")
            }

    assoc_set_ends: dict[str, dict[str, str]] = {}  # qualified association -> {role: entity set}
    entity_sets: list[dict] = []
    for schema in schemas:
        for container in _children(schema, "EntityContainer"):
            for aset in _children(container, "AssociationSet"):
                assoc_set_ends[aset.get("Association")] = {
                    e.get("Role"): e.get("EntitySet") for e in _children(aset, "End")
                }
            for es in _children(container, "EntitySet"):
                et = entity_types.get(es.get("EntityType"), {})
                entity_sets.append({
                    "name": es.get("Name"),
                    "entity_type": et.get("name"),
                    "label": _sap(es, "label"),
                    "keys": et.get("keys", []),
                    "properties": et.get("properties", []),
                    "navigation_properties": et.get("navigation_properties", []),
                })

    # Resolve each navigation property's target from the metadata's associations.
    for es in entity_sets:
        resolved = []
        for nav in es["navigation_properties"]:
            rel, role = nav["relationship"], nav["to_role"]
            resolved.append({
                "name": nav["name"],
                "target_entity_set": assoc_set_ends.get(rel, {}).get(role),
                "target_entity_type": _short(association_ends.get(rel, {}).get(role)),
                "relationship": _short(rel),
            })
        es["navigation_properties"] = resolved

    return {"service": service, "entity_sets": entity_sets}


def describe_service(client: SapClient, service: str) -> dict:
    resp = client.get(f"{service}/$metadata", accept="application/xml")
    return parse_metadata(resp.content, service)


def save_metadata(meta: dict, directory: Path | None = None) -> Path:
    out_dir = directory or METADATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{meta['service']}.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
