"""Detect cross-service references from metadata alone (no hardcoded SAP mappings).

An "anchor" entity is the entity set that owns a business key: among the entity sets whose
single key property is named K, the shortest one whose name contains K (A_Product for
key 'Product'). A property in *another* service refers to an anchor when its name or
label matches the anchor's key, the labels that key carries elsewhere in its own service
(Product is labelled 'Material' in most Product entities), or - weakest - the head noun
of the name matches (SoldToParty ~ BusinessPartner: party ~ partner).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .text import same_stem, squash, words

MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Reference:
    from_service: str
    from_entity: str
    property: str
    to_service: str
    to_entity: str
    confidence: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Anchor:
    service: str
    entity: str
    key: str
    key_words: list[str]
    labels: frozenset[str]      # squashed labels of every property named `key` in its service
    label_words: list[list[str]]
    type: str
    max_length: int | None


def find_anchors(metas: list[dict]) -> list[Anchor]:
    anchors = []
    for meta in metas:
        by_key: dict[str, list[dict]] = {}
        for es in meta["entity_sets"]:
            if len(es["keys"]) == 1:
                by_key.setdefault(es["keys"][0], []).append(es)
        for key, group in by_key.items():
            owners = [es for es in group if squash(key) in squash(es["name"])]
            if not owners:
                continue
            root = min(owners, key=lambda es: len(es["name"]))
            labels = {p["label"] for es in meta["entity_sets"] for p in es["properties"]
                      if p["name"] == key and p["label"]}
            key_prop = next(p for p in root["properties"] if p["name"] == key)
            anchors.append(Anchor(
                service=meta["service"], entity=root["name"], key=key, key_words=words(key),
                labels=frozenset(squash(lb) for lb in labels), label_words=[words(lb) for lb in labels],
                type=key_prop["type"], max_length=key_prop.get("max_length"),
            ))
    return anchors


def _score(prop: dict, anchor: Anchor) -> tuple[float, str] | None:
    if prop["type"] != anchor.type:
        return None
    name_w, label_w = words(prop["name"]), words(prop.get("label"))
    if squash(prop["name"]) == squash(anchor.key):
        score, reason = 0.9, f"name equals key '{anchor.key}'"
    elif prop.get("label") and squash(prop["label"]) in anchor.labels:
        score, reason = 0.8, f"label '{prop['label']}' is a label of '{anchor.key}'"
    elif len(name_w) > 1 and name_w[-len(anchor.key_words):] == anchor.key_words:
        score, reason = 0.7, f"name ends with key '{anchor.key}'"
    else:
        heads = [h for h in (name_w[-1:], label_w[-1:]) if h]
        anchor_heads = [anchor.key_words[-1]] + [lw[-1] for lw in anchor.label_words if lw]
        if not any(same_stem(h[0], a) for h in heads for a in anchor_heads):
            return None
        score, reason = 0.6, f"head word of '{prop['name']}' resembles '{anchor.key}'"
    # Reference fields hold the key's values, so lengths should agree.
    if prop.get("max_length") and anchor.max_length:
        score += 0.05 if prop["max_length"] == anchor.max_length else -0.3
    return round(score, 2), reason


def detect_references(metas: list[dict], min_confidence: float = MIN_CONFIDENCE) -> list[Reference]:
    anchors = find_anchors(metas)
    found = []
    for meta in metas:
        for es in meta["entity_sets"]:
            for prop in es["properties"]:
                for anchor in anchors:
                    if anchor.service == meta["service"]:
                        continue
                    hit = _score(prop, anchor)
                    if hit and hit[0] >= min_confidence:
                        found.append(Reference(meta["service"], es["name"], prop["name"],
                                               anchor.service, anchor.entity, hit[0], hit[1]))
    return sorted(found, key=lambda r: (-r.confidence, r.from_entity, r.property, r.to_entity))
