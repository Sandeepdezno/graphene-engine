"""Offline tests for the knowledge layer. Neo4j is replaced by FakeStore."""
from pathlib import Path

import pytest

from knowledge.builder import build_graph, load_metadata, to_rows, wipe_service
from knowledge.lookup import (
    EntityNotFound, _steps, describe_entity, find_entities, path_between, rank_entities,
)
from knowledge.references import detect_references, find_anchors
from knowledge.store import GraphError, Neo4jConfig
from knowledge.text import same_stem, words

META_DIR = Path(__file__).parent / "fixtures" / "metadata"


class FakeStore:
    """Records Cypher calls; `responses` maps a substring of the query to canned rows."""
    def __init__(self, responses=None):
        self.calls, self.responses = [], responses or {}

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        for needle, rows in self.responses.items():
            if needle in cypher:
                return rows
        return []


def prop(name, type_="Edm.String", label=None, max_length=10):
    return {"name": name, "type": type_, "label": label, "nullable": True, "max_length": max_length}


def entity(name, keys, props, navs=()):
    return {"name": name, "entity_type": name + "Type", "label": None, "keys": keys,
            "properties": props, "navigation_properties": list(navs)}


@pytest.fixture(scope="module")
def real_metas():
    return load_metadata(META_DIR)


# --- text helpers -----------------------------------------------------------

def test_words_split_camel_labels_and_plurals():
    assert words("SoldToParty") == ["sold", "to", "party"]
    assert words("Sold-to Party") == ["sold", "to", "party"]
    assert words("A_SalesOrder") == ["a", "sale", "order"]
    assert words("sales orders") == ["sale", "order"]
    assert words(None) == []


def test_same_stem():
    assert same_stem("party", "partner") and same_stem("order", "order")
    assert not same_stem("partner", "function") and not same_stem("sold", "sale")


# --- parsing metadata into graph rows --------------------------------------

def test_to_rows_ids_keys_and_navigations(real_metas):
    meta = next(m for m in real_metas if m["service"] == "API_SALES_ORDER_SRV")
    rows = to_rows(meta)
    assert len(rows["entities"]) == 22
    ids = {e["id"] for e in rows["entities"]}
    assert "API_SALES_ORDER_SRV.A_SalesOrder" in ids
    keyed = {p["id"] for p in rows["properties"] if p["is_key"]}
    assert "API_SALES_ORDER_SRV.A_SalesOrder.SalesOrder" in keyed
    assert "API_SALES_ORDER_SRV.A_SalesOrder.SoldToParty" not in keyed
    assert all(n["from_id"] in ids and n["to_id"] in ids for n in rows["navigations"])
    assert any(n["name"] == "to_Item" for n in rows["navigations"])
    assert len({p["id"] for p in rows["properties"]}) == len(rows["properties"])


def test_load_metadata_unknown_service_is_clear_error():
    with pytest.raises(FileNotFoundError, match="API_NOPE"):
        load_metadata(META_DIR, ["API_NOPE"])


# --- cross-service detection -----------------------------------------------

def test_anchors_are_found_from_keys_only(real_metas):
    anchors = {a.entity: a for a in find_anchors(real_metas)}
    assert {"A_BusinessPartner", "A_Customer", "A_Supplier", "A_Product", "A_SalesOrder"} <= set(anchors)
    assert "A_ProductSales" not in anchors and "A_BPCreditWorthiness" not in anchors
    assert "material" in anchors["A_Product"].labels  # label the key carries elsewhere in its service


def test_real_metadata_references(real_metas):
    refs = {(r.from_entity, r.property, r.to_entity): r for r in detect_references(real_metas)}
    assert refs[("A_SalesOrder", "SoldToParty", "A_BusinessPartner")].confidence >= 0.6
    assert refs[("A_SalesOrderItem", "Material", "A_Product")].confidence >= 0.8
    assert refs[("A_SalesOrderItemPartner", "Customer", "A_Customer")].confidence >= 0.9
    # a code that merely contains the word 'partner' is not a reference
    assert not any(p == "PartnerFunction" for (_, p, _) in refs)
    # references only ever cross services
    assert all(r.from_service != r.to_service for r in refs.values())


def test_detection_on_synthetic_unseen_system():
    """No SAP names in the code: a made-up system links the same way."""
    a = {"service": "SVC_A", "entity_sets": [
        entity("Client", ["Client"], [prop("Client")]),
        entity("ClientAddr", ["Client", "Seq"], [prop("Client", label="Customer"), prop("Seq")])]}
    b = {"service": "SVC_B", "entity_sets": [
        entity("Invoice", ["Id"], [prop("Id"), prop("BillToClient", label="Bill-to Client"),
                                   prop("Buyer", label="Customer"),
                                   prop("Notes", max_length=200), prop("Amount", "Edm.Decimal", max_length=None)])]}
    refs = {r.property: r for r in detect_references([a, b])}
    assert set(refs) == {"BillToClient", "Buyer"}
    assert all(r.to_entity == "Client" for r in refs.values())
    assert refs["Buyer"].confidence > refs["BillToClient"].confidence - 0.2 and refs["Buyer"].confidence >= 0.5


def test_type_and_length_mismatch_reduce_or_drop():
    a = {"service": "A", "entity_sets": [entity("Thing", ["Thing"], [prop("Thing", max_length=10)])]}
    b = {"service": "B", "entity_sets": [entity("Use", ["Id"], [
        prop("Id"), prop("Thing", max_length=10), prop("OtherThing", max_length=35),
        prop("ThingCount", "Edm.Int32", max_length=None)])]}
    got = {r.property: r.confidence for r in detect_references([a, b])}
    assert got == {"Thing": 0.95}


# --- building ---------------------------------------------------------------

def test_build_wipes_each_service_before_loading_and_uses_merge(real_metas):
    store = FakeStore({"RETURN s.name AS name": [{"name": m["service"]} for m in real_metas]})
    result = build_graph(store, META_DIR)
    cypher = [c for c, _ in store.calls]
    assert sum("CREATE CONSTRAINT" in c for c in cypher) == 3
    assert sum("DETACH DELETE" in c for c in cypher) == 3
    assert not any(c.strip().startswith("CREATE (") for c in cypher)  # MERGE only -> idempotent
    first_wipe = next(i for i, c in enumerate(cypher) if "DETACH DELETE" in c)
    first_load = next(i for i, c in enumerate(cypher) if "MERGE (e:EntitySet" in c)
    assert first_wipe < first_load
    assert {"API_SALES_ORDER_SRV", "API_BUSINESS_PARTNER", "API_PRODUCT_SRV"} == set(result["services"])
    ref_call = next(p for c, p in store.calls if "REFERENCES" in c and "MERGE" in c)
    assert len(ref_call["rows"]) == len(result["references"]) > 0


def test_single_service_build_only_wipes_that_service(real_metas):
    store = FakeStore({"RETURN s.name AS name": [{"name": m["service"]} for m in real_metas]})
    build_graph(store, META_DIR, services=["API_PRODUCT_SRV"])
    wipes = [p for c, p in store.calls if "DETACH DELETE" in c]
    assert wipes == [{"service": "API_PRODUCT_SRV"}]


def test_wipe_service_targets_one_service():
    store = FakeStore()
    wipe_service(store, "API_X")
    assert store.calls[0][1] == {"service": "API_X"}


# --- find_entities ----------------------------------------------------------

def doc(name, keys=(), fields=(), navs=(), service="S", indegree=0):
    props = [{"name": k, "label": k, "is_key": True} for k in keys]
    props += [{"name": f, "label": f, "is_key": False} for f in fields]
    return {"name": name, "service": service, "label": None, "props": props,
            "navs": [{"name": n, "target": t} for n, t in navs], "indegree": indegree}


def test_exact_name_beats_everything_and_plural_works():
    docs = [doc("A_SalesOrderItem", ["SalesOrder", "SalesOrderItem"]),
            doc("A_SalesOrder", ["SalesOrder"]), doc("A_Product", ["Product"])]
    ranked = rank_entities("sales orders", docs)
    assert [r["name"] for r in ranked] == ["A_SalesOrder", "A_SalesOrderItem"]


def test_parent_exposing_term_outranks_detail_tables():
    docs = [doc("A_Customer", ["Customer"]),
            doc("A_CustomerCompany", ["Customer", "CompanyCode"]),
            doc("A_CustomerDunning", ["Customer", "Dunning"]),
            doc("A_BusinessPartner", ["BusinessPartner"], ["Customer"], [("to_Customer", "A_Customer")], indegree=20),
            doc("A_Unrelated", ["Foo"], ["Bar"])]
    names = [r["name"] for r in rank_entities("customer", docs)]
    assert names[0] == "A_Customer" and names.index("A_BusinessPartner") <= 1
    assert "A_Unrelated" not in names


def test_no_match_and_stopwords_only():
    assert rank_entities("zzz", [doc("A_Product", ["Product"])]) == []
    assert rank_entities("show me all", [doc("A_Product", ["Product"])]) == []


def test_find_entities_uses_store():
    store = FakeStore({"RETURN e.name AS name, e.service": [doc("A_Product", ["Product"])]})
    assert find_entities(store, "product")[0]["name"] == "A_Product"


# --- describe_entity --------------------------------------------------------

def test_describe_entity_collects_one_hop():
    entity_row = {"id": "S.A_Item", "name": "A_Item", "service": "S", "label": None, "entity_type": "T",
                  "fields": [{"name": "Material", "type": "Edm.String", "label": "Material", "is_key": False,
                              "position": 1, "references": [{"entity": "A_Product", "service": "P", "confidence": 0.85}]},
                             {"name": "Item", "type": "Edm.String", "label": "Item", "is_key": True,
                              "position": 5, "references": []}],
                  "navigates_to": [{"name": "to_Order", "entity": "A_Order"}], "navigated_from": []}
    store = FakeStore({"RETURN e.id AS id": [entity_row],
                       "REFERENCED_BY_MARKER": [], "(o:EntitySet)-[:HAS_PROPERTY]": [
                           {"entity": "A_X", "service": "S", "property": "Item", "confidence": 0.9}]})
    result = describe_entity(store, "a_item")
    assert result["keys"] == ["Item"]
    assert [f["name"] for f in result["fields"]] == ["Item", "Material"]  # keys first
    assert result["fields"][1]["references"][0]["entity"] == "A_Product"
    assert result["referenced_by"][0]["entity"] == "A_X"
    assert result["navigates_to"] == [{"name": "to_Order", "entity": "A_Order"}]


def test_describe_unknown_entity_suggests():
    store = FakeStore({"RETURN e.name AS name, e.service": [doc("A_Product", ["Product"])]})
    with pytest.raises(EntityNotFound, match="A_Product"):
        describe_entity(store, "A_Produkt")


# --- path_between -----------------------------------------------------------

def test_steps_collapse_property_hops_in_both_directions():
    nodes = [{"id": "S.A_Order", "name": "A_Order", "kind": "EntitySet"},
             {"id": "S.A_Item", "name": "A_Item", "kind": "EntitySet"},
             {"id": "S.A_Item.Material", "name": "Material", "kind": "Property"},
             {"id": "P.A_Product", "name": "A_Product", "kind": "EntitySet"}]
    rels = [{"type": "NAVIGATES_TO", "name": "to_Order", "confidence": None, "start": "S.A_Item"},
            {"type": "HAS_PROPERTY", "name": None, "confidence": None, "start": "S.A_Item"},
            {"type": "REFERENCES", "name": None, "confidence": 0.85, "start": "S.A_Item.Material"}]
    steps = _steps(nodes, rels)
    assert steps == [
        {"kind": "navigation", "from": "A_Order", "to": "A_Item", "via": "to_Order", "forward": False},
        {"kind": "reference", "from": "A_Item", "to": "A_Product", "via": "Material", "forward": True,
         "confidence": 0.85}]
    reverse = _steps(nodes[:0:-1], rels[:0:-1][::1])  # Product -> Material <- Item
    assert reverse[0]["kind"] == "reference" and reverse[0]["forward"] is False and reverse[0]["to"] == "A_Item"


def test_path_between_and_errors():
    nodes = [{"id": "a", "name": "A", "kind": "EntitySet"}, {"id": "b", "name": "B", "kind": "EntitySet"}]
    rels = [{"type": "NAVIGATES_TO", "name": "to_B", "confidence": None, "start": "a"}]
    store = FakeStore({"shortestPath": [{"nodes": nodes, "rels": rels}]})
    path = path_between(store, "A", "B")
    assert path["from"] == "A" and path["steps"][0]["via"] == "to_B"
    assert "*..9" in store.calls[0][0]

    disconnected = FakeStore({"RETURN toLower(e.name)": [{"name": "a"}, {"name": "b"}]})
    assert path_between(disconnected, "A", "B") is None

    missing = FakeStore({"RETURN toLower(e.name)": [{"name": "a"}]})
    with pytest.raises(EntityNotFound, match="'B'"):
        path_between(missing, "A", "B")


# --- config -----------------------------------------------------------------

def test_neo4j_config(monkeypatch, tmp_path):
    for k in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(GraphError, match="NEO4J_PASSWORD"):
        Neo4jConfig.from_env(tmp_path / "none.env")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw12345678")
    cfg = Neo4jConfig.from_env(tmp_path / "none.env")
    assert cfg.uri.startswith("bolt://") and "pw12345678" not in repr(cfg)
