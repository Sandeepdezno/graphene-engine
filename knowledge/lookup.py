"""Query the schema graph: find_entities, describe_entity, path_between."""
from __future__ import annotations

import difflib
import math

from .store import GraphStore
from .text import same_stem, words

STOPWORDS = {"the", "of", "all", "show", "me", "list", "get", "for", "to", "an", "in", "and", "my", "our"}
SUBORDINATE_FACTOR = 0.4  # detail tables (items, texts, per-area data) rank below their parent
HUB_WEIGHT = 0.4          # small boost for entities many others point to

_ENTITY_DOCS = """
MATCH (e:EntitySet)
RETURN e.name AS name, e.service AS service, e.label AS label,
  [(e)-[:HAS_PROPERTY]->(p:Property) | {name: p.name, label: p.label, is_key: p.is_key}] AS props,
  [(e)-[n:NAVIGATES_TO]->(t:EntitySet) | {name: n.name, target: t.name}] AS navs,
  size([(e)<-[:NAVIGATES_TO]-() | 1]) + size([(e)<-[:REFERENCES]-() | 1]) AS indegree
"""

_DESCRIBE = """
MATCH (e:EntitySet) WHERE toLower(e.name) = toLower($name) AND ($service IS NULL OR e.service = $service)
RETURN e.id AS id, e.name AS name, e.service AS service, e.label AS label, e.entity_type AS entity_type,
  [(e)-[:HAS_PROPERTY]->(p:Property) | {name: p.name, type: p.type, label: p.label, is_key: p.is_key, position: p.position,
     references: [(p)-[r:REFERENCES]->(t:EntitySet) |
        {entity: t.name, service: t.service, confidence: r.confidence}]}] AS fields,
  [(e)-[n:NAVIGATES_TO]->(t:EntitySet) | {name: n.name, entity: t.name}] AS navigates_to,
  [(s:EntitySet)-[n:NAVIGATES_TO]->(e) | {name: n.name, entity: s.name}] AS navigated_from
ORDER BY e.service LIMIT 1
"""

_REFERENCED_BY = """
MATCH (o:EntitySet)-[:HAS_PROPERTY]->(p:Property)-[r:REFERENCES]->(e:EntitySet {id: $id})
RETURN o.name AS entity, o.service AS service, p.name AS property, r.confidence AS confidence
ORDER BY r.confidence DESC, o.name
"""

_PATH = """
MATCH (a:EntitySet), (b:EntitySet)
WHERE toLower(a.name) = toLower($a) AND toLower(b.name) = toLower($b) AND a <> b
MATCH p = shortestPath((a)-[:NAVIGATES_TO|REFERENCES|HAS_PROPERTY*..%d]-(b))
RETURN [n IN nodes(p) | {id: n.id, name: n.name, kind: head(labels(n))}] AS nodes,
       [r IN relationships(p) | {type: type(r), name: r.name, confidence: r.confidence,
                                 start: startNode(r).id}] AS rels
ORDER BY length(p) LIMIT 1
"""

_EXISTING = "MATCH (e:EntitySet) WHERE toLower(e.name) IN $names RETURN toLower(e.name) AS name"


class EntityNotFound(Exception):
    def __init__(self, name: str, suggestions: list[str]):
        self.name, self.suggestions = name, suggestions
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"No entity set named '{name}' in the graph.{hint}")


# --- find_entities ----------------------------------------------------------

def _coverage(query: list[str], texts: list[str | None]) -> float:
    """Fraction of query words found in the best single text (name, label, ...)."""
    best = 0.0
    for text in texts:
        w = [x for x in words(text) if len(x) > 1]
        hit = sum(1 for q in query if any(same_stem(q, x) for x in w))
        best = max(best, hit / len(query))
    return best


def _subordinates(docs: list[dict]) -> set[tuple[str, str]]:
    """Entities that are detail tables of another: their key contains another entity's key,
    or has the same key as an entity with a shorter name (A_ProductSales next to A_Product)."""
    keys = {(d["service"], d["name"]): frozenset(p["name"] for p in d["props"] if p["is_key"]) for d in docs}
    out = set()
    for (svc, name), k in keys.items():
        for (svc2, name2), k2 in keys.items():
            if svc == svc2 and name != name2 and k2 and (k2 < k or (k2 == k and len(name2) < len(name))):
                out.add((svc, name))
                break
    return out


def rank_entities(text: str, docs: list[dict], limit: int = 10) -> list[dict]:
    query = [w for w in words(text) if len(w) > 1 and w not in STOPWORDS]
    if not query:
        return []
    subordinate = _subordinates(docs)
    results = []
    for d in docs:
        name_w = [w for w in words(d["name"]) if len(w) > 1]
        why = []
        name_cov = _coverage(query, [d["name"], d.get("label")])
        score = 6 * name_cov
        if name_cov:
            why.append("name")
            score -= 0.3 * max(0, len(name_w) - len(query))
            if set(name_w) == set(query):
                score += 4
        key_cov = _coverage(query, [t for p in d["props"] if p["is_key"] for t in (p["name"], p["label"])])
        prop_cov = _coverage(query, [t for p in d["props"] if not p["is_key"] for t in (p["name"], p["label"])])
        nav_cov = _coverage(query, [n["name"] for n in d["navs"]])
        for cov, weight, tag in ((key_cov, 2, "key"), (prop_cov, 2.5, "field"), (nav_cov, 2.5, "navigation")):
            if cov:
                score += weight * cov
                why.append(tag)
        if score <= 0:
            continue
        if (d["service"], d["name"]) in subordinate:
            score *= SUBORDINATE_FACTOR
        score += HUB_WEIGHT * math.log1p(d.get("indegree", 0))
        results.append({"name": d["name"], "service": d["service"], "score": round(score, 2), "matched": why})
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results[:limit]


def find_entities(store: GraphStore, text: str, limit: int = 10) -> list[dict]:
    """Rank entity sets against a phrase such as 'sales orders' or 'customer'."""
    return rank_entities(text, store.run(_ENTITY_DOCS), limit)


def _suggest(store: GraphStore, name: str, n: int = 3) -> list[str]:
    """Close spellings first (typos), then phrase matches."""
    docs = store.run(_ENTITY_DOCS)
    close = difflib.get_close_matches(name, [d["name"] for d in docs], n=n, cutoff=0.6)
    ranked = [r["name"] for r in rank_entities(name, docs, n)]
    return list(dict.fromkeys(close + ranked))[:n]


# --- describe_entity --------------------------------------------------------

def describe_entity(store: GraphStore, name: str, service: str | None = None) -> dict:
    rows = store.run(_DESCRIBE, name=name, service=service)
    if not rows:
        raise EntityNotFound(name, _suggest(store, name))
    entity = rows[0]
    fields = sorted(entity.pop("fields"), key=lambda f: (not f["is_key"], f["position"] or 0))
    entity["keys"] = [f["name"] for f in fields if f["is_key"]]
    entity["fields"] = fields
    entity["referenced_by"] = store.run(_REFERENCED_BY, id=entity.pop("id"))
    return entity


# --- path_between -----------------------------------------------------------

def path_between(store: GraphStore, a: str, b: str, max_hops: int = 9) -> dict | None:
    """Shortest connection between two entity sets over navigations and cross-service references.
    Returns {"from", "to", "steps": [...]}, or None when they are not connected."""
    rows = store.run(_PATH % int(max_hops), a=a, b=b)
    if not rows:
        found = {r["name"] for r in store.run(_EXISTING, names=[a.lower(), b.lower()])}
        for n in (a, b):
            if n.lower() not in found:
                raise EntityNotFound(n, _suggest(store, n))
        return None
    nodes, rels = rows[0]["nodes"], rows[0]["rels"]
    return {"from": nodes[0]["name"], "to": nodes[-1]["name"], "steps": _steps(nodes, rels)}


def _steps(nodes: list[dict], rels: list[dict]) -> list[dict]:
    """Collapse Entity-Property-Entity triples into one 'reference' step."""
    steps, i = [], 0
    while i < len(rels):
        a, rel = nodes[i], rels[i]
        if rel["type"] == "NAVIGATES_TO":
            b = nodes[i + 1]
            steps.append({"kind": "navigation", "from": a["name"], "to": b["name"],
                          "via": rel["name"], "forward": rel["start"] == a["id"]})
            i += 1
        else:  # HAS_PROPERTY (a owns the property) or REFERENCES (the property belongs to the next entity)
            prop, b, second = nodes[i + 1], nodes[i + 2], rels[i + 1]
            ref = rel if rel["type"] == "REFERENCES" else second
            steps.append({"kind": "reference", "from": a["name"], "to": b["name"], "via": prop["name"],
                          "forward": rel["type"] == "HAS_PROPERTY", "confidence": ref["confidence"]})
            i += 2
    return steps
