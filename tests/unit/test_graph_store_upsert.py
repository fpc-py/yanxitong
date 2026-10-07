"""GraphStore unit tests: deterministic ids, batch write shapes, review sampling.

Runs against a recording fake ``_run`` — no live Neo4j required.
"""

from src.knowledge.graph_store import (
    GraphStore,
    make_edge_key,
    make_entity_id,
    normalize_entity_name,
    sample_needs_review,
)


def test_entity_id_deterministic_and_normalized():
    assert normalize_entity_name("Federated  Learning!") == "federated learning"
    assert make_entity_id("FedProx") == make_entity_id("fedprox ")
    assert make_entity_id("Federated  Learning!") == make_entity_id("federated learning")
    assert make_entity_id("Federated Learning") != make_entity_id("FedAvg")
    assert make_entity_id("X").startswith("e:")


def test_edge_key_stable():
    k1 = make_edge_key("ax:2401.1", "PROPOSES", "e:abc")
    assert k1 == make_edge_key("ax:2401.1", "PROPOSES", "e:abc")
    assert len(k1) == 16
    assert make_edge_key("ax:2401.1", "USES_DATASET", "e:abc") != k1


def test_review_sampling_deterministic_and_bounded():
    first = [sample_needs_review(f"key{i}", 0.1) for i in range(1000)]
    assert first == [sample_needs_review(f"key{i}", 0.1) for i in range(1000)]
    ratio = sum(first) / len(first)
    assert 0.05 < ratio < 0.2
    assert sample_needs_review("k", 0.0) is False
    assert sample_needs_review("k", 1.0) is True


class _Recorder:
    def __init__(self, results=None):
        self.calls: list[tuple[str, dict]] = []
        self._results = results or {}

    async def __call__(self, query, params=None):
        self.calls.append((query, params or {}))
        for needle, result in self._results.items():
            if needle in query:
                return result
        return []

    def find(self, needle: str):
        return [(q, p) for q, p in self.calls if needle in q]


async def _store_with_recorder(results=None):
    store = GraphStore()
    recorder = _Recorder(results)
    store._run = recorder  # type: ignore[method-assign]
    store._constraints_ready = True
    return store, recorder


async def test_upsert_papers_batch_merge_shape():
    store, rec = await _store_with_recorder()
    out = await store.upsert_papers([
        {"paper_id": "ax:2401.00001", "title": "T", "authors": ["A"], "year": 2024,
         "title_hash": "abc123", "abstract": "x" * 10},
        {"title": "no id — skipped"},
    ])
    assert out["papers"] == 1
    calls = rec.find("MERGE (p:Paper")
    assert len(calls) == 1
    query, params = calls[0]
    rows = params["rows"]
    assert len(rows) == 1 and rows[0]["paper_id"] == "ax:2401.00001"
    assert "ts" not in rows[0] and "session_id" not in rows[0]
    assert "SET p.created_at = coalesce(p.created_at, $ts)" in query


async def test_upsert_edges_groups_by_labels_and_sets_edge_key():
    store, rec = await _store_with_recorder()
    written = await store.upsert_edges(
        [
            {"source_id": "ax:2401.00001", "target_id": "e:aaa", "type": "PROPOSES",
             "properties": {"evidence": "we propose"}},
            {"source_id": "e:aaa", "target_id": "e:bbb", "type": "EXTENDS", "properties": {}},
            {"source_id": "", "target_id": "e:bbb", "type": "EXTENDS", "properties": {}},
        ],
        session_id="sess-1",
    )
    assert written == 2
    paper_calls = rec.find("MATCH (a:Paper {paper_id: row.source_id}), (b:Entity {entity_id: row.target_id})")
    assert len(paper_calls) == 1
    query, params = paper_calls[0]
    row = params["rows"][0]
    assert row["edge_key"] == make_edge_key("ax:2401.00001", "PROPOSES", "e:aaa")
    assert isinstance(row["needs_review"], bool)
    assert row["session_id"] == "sess-1"
    assert "r.sessions = coalesce(r.sessions, [])" in query
    assert "coalesce(r.needs_review, row.needs_review)" in query
    entity_calls = rec.find("MATCH (a:Entity {entity_id: row.source_id})")
    assert len(entity_calls) == 1


async def test_upsert_entities_merges_on_entity_label_only():
    store, rec = await _store_with_recorder()
    written = await store.upsert_entities(
        [{"entity_id": make_entity_id("FedProx"), "name": "FedProx", "type": "Method"}],
        session_id="sess-1",
    )
    assert written == 1
    query, _ = rec.find("MERGE (e:Entity")[0]
    assert "MERGE (e:Entity {entity_id: row.entity_id})" in query
    assert "SET e:Method" in query
    assert "e.created_at = coalesce(e.created_at, $ts)" in query


async def test_link_session_creates_session_and_retrieved_edges():
    store, rec = await _store_with_recorder()
    assert await store.link_session("sess-1", ["ax:1", ""]) == 1
    assert rec.find("MERGE (s:Session {session_id: $sid})")
    retrieved = rec.find("MERGE (s)-[r:RETRIEVED]->(p)")
    assert len(retrieved) == 1
    assert retrieved[0][1]["ids"] == ["ax:1"]


async def test_collapse_title_duplicate_rewires_and_deletes():
    store, rec = await _store_with_recorder(results={
        "dup.paper_id AS dup_id": [{"dup_id": "th:abc123"}],
        "MATCH (dup:Paper {paper_id: $dup})-[r]->(other)": [
            {"rel": "PROPOSES", "props": {"evidence": "e"}, "other_id": "e:aaa"}
        ],
        "MATCH (other)-[r]->(dup:Paper {paper_id: $dup})": [
            {"rel": "CITES", "props": {}, "other_id": "ax:9"}
        ],
    })
    collapsed = await store._collapse_title_duplicate("ax:2401.00001", "abc123")
    assert collapsed == 1
    written_rows = [row for _, p in rec.find("MERGE (a)-[r:") for row in p["rows"]]
    sources = {row["source_id"] for row in written_rows}
    targets = {row["target_id"] for row in written_rows}
    assert sources == {"ax:2401.00001", "ax:9"}
    assert targets == {"e:aaa", "ax:2401.00001"}
    assert rec.find("DETACH DELETE dup")


async def test_session_subgraph_parses_json_payload():
    """Regression: get_subgraph returns a JSON string; session_subgraph must parse it."""
    import json as _json

    store, _ = await _store_with_recorder(results={
        "RETRIEVED]->(p:Paper)": [
            {"paper_id": "ax:1", "title": "T1", "year": 2024, "arxiv_id": "2401.1"},
        ],
        "RETRIEVED]->(:Paper)--(e:Entity)": [{"eid": "e:aaa"}],
    })

    async def fake_subgraph(ids):
        assert set(ids) == {"ax:1", "e:aaa"}
        return _json.dumps({
            "nodes": [
                {"id": "ax:1", "name": "T1", "type": "Paper", "kind": "paper"},
                {"id": "e:aaa", "name": "FedProx", "type": "Method", "kind": "entity"},
            ],
            "edges": [{"source": "ax:1", "target": "e:aaa", "type": "PROPOSES", "properties": {}}],
        })

    store.get_subgraph = fake_subgraph  # type: ignore[method-assign]
    sub = await store.session_subgraph("sess-1")
    assert {n["id"] for n in sub["nodes"]} == {"ax:1", "e:aaa"}
    assert sub["edges"][0]["type"] == "PROPOSES"


async def test_purge_legacy_dry_run_keeps_kb_and_new_model_nodes():
    store, rec = await _store_with_recorder(results={"RETURN count(n) AS n": [{"n": 355}]})
    out = await store.purge_legacy()  # dry-run by default
    assert out == {"matched": 355, "deleted": 0, "dry_run": True}
    query, _ = rec.find("MATCH (n) WHERE NOT n:Paper")[0]
    assert "NOT n:Paper AND NOT n:Entity AND NOT n:Session" in query
    # coalesce 必须存在：旧节点 paper_id 为 null，裸 STARTS WITH 会把整行 WHERE 置 null 漏删
    assert "coalesce(n.entity_id, '') STARTS WITH 'kb:'" in query
    assert "coalesce(n.paper_id, '') STARTS WITH 'kb:'" in query
    assert "DETACH DELETE" not in query
    assert rec.find("RETURN count(n) AS n")


async def test_contradictions_dedupes_symmetric_pairs():
    store, _ = await _store_with_recorder(results={
        "CONTRADICTS": [
            {"source": "A", "target": "B", "evidence": "x", "evidence_source": "s1"},
            {"source": "B", "target": "A", "evidence": "x", "evidence_source": "s1"},
            {"source": "B", "target": "C", "evidence": "y", "evidence_source": "s2"},
        ],
    })
    rows = await store.contradictions()
    assert [(r["source"], r["target"]) for r in rows] == [("A", "B"), ("B", "C")]


async def test_mark_reviewed_approve_keeps_edge_reject_deletes():
    store, rec = await _store_with_recorder(results={"RETURN count(r) AS n": [{"n": 1}]})
    assert await store.mark_reviewed("k1", "approved", note="ok", reviewer="user:3") is True
    approve = rec.find("SET r.reviewed = true")[0]
    assert approve[1]["decision"] == "approved" and approve[1]["note"] == "ok"
    assert not rec.find("DELETE r")

    store2, rec2 = await _store_with_recorder(results={"RETURN count(r) AS n": [{"n": 1}]})
    assert await store2.mark_reviewed("k2", "rejected") is True
    assert rec2.find("DELETE r")

    store3, rec3 = await _store_with_recorder(results={"RETURN count(r) AS n": [{"n": 0}]})
    assert await store3.mark_reviewed("missing", "approved") is False
    assert not rec3.find("SET r.reviewed")
