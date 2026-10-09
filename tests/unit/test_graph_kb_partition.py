"""图谱分区单元测试：kb 过滤谓词、主包+跨包列表、写路径 kb 归属（无 Neo4j）。

用记录式 fake ``_run`` 断言下发到 Cypher 的谓词与参数：``kb_id=None`` 时
与旧版完全一致（零谓词）；``"default"`` 显式含未迁移（kb_id IS NULL）数据。
"""

from src.knowledge.graph_store import GraphStore, kb_filter, kb_filter_multi

KB_A = "kb_aaaaaaaa"
KB_B = "kb_bbbbbbbb"


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


def _store(results=None):
    store = GraphStore()
    rec = _Recorder(results)
    store._run = rec  # type: ignore[method-assign]
    store._constraints_ready = True
    return store, rec


def test_kb_filter_predicates():
    one = kb_filter("p")
    assert "$kb IS NULL" in one and "p.kb_id = $kb" in one
    assert "p.kb_id IS NULL AND $kb = 'default'" in one
    multi = kb_filter_multi("e", "kbs")
    assert "$kbs IS NULL" in multi and "e.kb_id IN $kbs" in multi
    assert "'default' IN $kbs" in multi


async def test_search_entities_multi_legacy_no_filter():
    rows = [{"e": {"name": "X", "kb_id": KB_A}, "kw_hits": 2}]
    store, rec = _store({"RETURN e, size(": rows})
    out = await store.search_entities_multi(["x"])
    query, params = rec.find("MATCH (e:Entity)")[0]
    assert "kb_id IN" not in query and params["kbs"] is None
    assert out[0]["weight"] == 1.0 and out[0]["score"] == 2.0


async def test_search_entities_multi_primary_plus_cross_list():
    rows = [
        {"e": {"name": "本包实体", "kb_id": KB_A}, "kw_hits": 1},
        {"e": {"name": "跨包实体", "kb_id": KB_B}, "kw_hits": 1},
        {"e": {"name": "遗留实体"}, "kw_hits": 1},  # 无 kb_id → default
    ]
    store, rec = _store({"RETURN e, size(": rows})
    out = await store.search_entities_multi(
        ["e"], kb_id=KB_A, cross_kb_ids=[KB_B, KB_A, "", KB_B]
    )
    query, params = rec.find("MATCH (e:Entity)")[0]
    assert params["kbs"] == [KB_A, KB_B]  # 去重、剔除主包重复与空串
    assert "IN $kbs" in query and "'default' IN $kbs" in query
    scores = {e["name"]: (e["weight"], e["score"], e["kb_id"]) for e in out}
    assert scores["本包实体"] == (1.0, 1.0, KB_A)
    assert scores["跨包实体"] == (0.85, 0.85, KB_B)
    assert scores["遗留实体"] == (0.85, 0.85, "default")
    assert out[0]["name"] == "本包实体"  # 主包命中按 1.0 排前


async def test_search_entities_multi_scope_session_variant():
    store, rec = _store()
    await store.search_entities_multi(["x"], scope="s1", kb_id=KB_A)
    query, params = rec.find("Session {session_id: $scope}")[0]
    assert params["scope"] == "s1" and params["kbs"] == [KB_A]
    assert "IN $kbs" in query


async def test_papers_existing_kb_kwarg():
    store, rec = _store({"MATCH (p:Paper)": [{"paper_id": "ax:kb_aaaaaaaa:2401.1"}]})
    out = await store.papers_existing(["ax:x"], kb_id="default")
    query, params = rec.find("MATCH (p:Paper)")[0]
    assert params == {"ids": ["ax:x"], "kb": "default"}
    assert "p.kb_id IS NULL AND $kb = 'default'" in query
    assert out == ["ax:kb_aaaaaaaa:2401.1"]
    await store.papers_existing(["ax:x"])
    assert rec.find("MATCH (p:Paper)")[1][1]["kb"] is None


async def test_upsert_entities_writes_pack_membership():
    store, rec = _store()
    n = await store.upsert_entities(
        [{"entity_id": f"e:{KB_A}:abc", "name": "FedProx", "type": "Method"}], kb_id=KB_A
    )
    query, params = rec.find("MERGE (e:Entity")[0]
    assert n == 1 and params["kb"] == KB_A
    assert "e.kb_id = coalesce(e.kb_id, $kb)" in query
    await store.upsert_entities([{"entity_id": "e:legacy", "name": "X", "type": "Method"}])
    assert rec.find("MERGE (e:Entity")[1][1]["kb"] == "default"  # 空 kb → 默认包


async def test_upsert_edges_writes_pack_membership():
    store, rec = _store()
    await store.upsert_edges(
        [{"source_id": "p:1", "target_id": "e:2", "type": "CITES", "properties": {}}],
        kb_id=KB_B,
    )
    kb_calls = [(q, p) for q, p in rec.calls if "r.kb_id" in q]
    assert kb_calls and kb_calls[0][1]["kb"] == KB_B
