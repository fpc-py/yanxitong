"""知识库块级领域标签单元测试：FakeEncoder 下的按包召回与统计。

标签语义：未打标签（``kb_id=""``）= 通用，所有领域包均可召回；
打了标签 = 仅对应领域包的问答召回；``kb_id=""`` 查询保持旧行为全量召回。
"""

import pytest

from src.knowledge import kb as kb_mod
from src.knowledge.vector_store import VectorStore

KB_A = "kb_aaaaaaaa"
KB_B = "kb_bbbbbbbb"


def _chunks(*texts: str) -> list[dict]:
    return [
        {"text": t, "page": i + 1, "para": i, "section_title": "Body",
         "char_start": 0, "char_end": len(t), "chunk_index": i}
        for i, t in enumerate(texts)
    ]


@pytest.fixture()
def env(tmp_path, monkeypatch, fake_encoder_cls):
    store = VectorStore(dim=8, encoder=fake_encoder_cls())
    monkeypatch.setattr(kb_mod, "_kb_store", store)
    monkeypatch.setattr(kb_mod, "KB_DIR", str(tmp_path / "kb"))
    return kb_mod


def test_query_chunks_pack_filter(env):
    env.add_chunks("a.pdf", _chunks("pack A private topic " * 20), "personal", "user:1", "h1", kb_id=KB_A)
    env.add_chunks("b.pdf", _chunks("pack B private topic " * 20), "personal", "user:1", "h2", kb_id=KB_B)
    env.add_chunks("gen.pdf", _chunks("generic shared topic " * 20), "personal", "user:1", "h3")

    scopes = ["kb:user:1"]
    hits_a = env.query_chunks("topic", scopes, top_k=10, kb_id=KB_A)
    assert {h["filename"] for h in hits_a} == {"a.pdf", "gen.pdf"}  # 通用块任何包可见
    hits_b = env.query_chunks("topic", scopes, top_k=10, kb_id=KB_B)
    assert {h["filename"] for h in hits_b} == {"b.pdf", "gen.pdf"}
    bare = env.query_chunks("topic", scopes, top_k=10)
    assert {h["filename"] for h in bare} == {"a.pdf", "b.pdf", "gen.pdf"}  # 旧行为全量


def test_tag_stored_alongside_scope(env):
    env.add_chunks("a.pdf", _chunks("alpha " * 30), "personal", "user:2", "h1", kb_id=KB_A)
    doc = next(iter(env.get_kb_store()._documents.values()))
    assert doc["kb_id"] == KB_A
    assert doc["scope"] == "kb:user:2"

    env.add_chunks("g.pdf", _chunks("generic " * 30), "personal", "user:2", "h2")
    generic = next(d for d in env.get_kb_store()._documents.values() if d["filename"] == "g.pdf")
    assert generic["kb_id"] == ""


def test_count_chunks_by_kb(env):
    env.add_chunks("a.pdf", _chunks("one " * 30, "two " * 30), "personal", "user:1", "h1", kb_id=KB_A)
    env.add_chunks("g.pdf", _chunks("generic " * 30), "personal", "user:1", "h2")
    assert env.count_chunks_by_kb(KB_A) == 2
    assert env.count_chunks_by_kb(KB_B) == 0
    assert env.count_chunks_by_kb("") == 0
