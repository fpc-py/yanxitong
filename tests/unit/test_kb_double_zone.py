"""Unit tests for the dual-zone knowledge base (team shared + personal)."""

import hashlib

import pytest

from src.knowledge import kb as kb_mod
from src.knowledge.vector_store import VectorStore


def _chunks(*texts: str) -> list[dict]:
    return [
        {"text": t, "page": i + 1, "para": i, "section_title": "Body",
         "char_start": 0, "char_end": len(t), "chunk_index": i}
        for i, t in enumerate(texts)
    ]


@pytest.fixture()
def kb_env(tmp_path, monkeypatch, fake_encoder_cls):
    store = VectorStore(dim=8, encoder=fake_encoder_cls())
    monkeypatch.setattr(kb_mod, "_kb_store", store)
    monkeypatch.setattr(kb_mod, "KB_DIR", str(tmp_path / "kb"))
    return kb_mod


def test_scope_naming(kb_env):
    assert kb_env.scope_of("team", "user:1") == "kb:team"
    assert kb_env.scope_of("personal", "user:1") == "kb:user:1"
    assert kb_env.scope_of("personal", "anon:abc") == "kb:anon:abc"


def test_double_zone_visibility(kb_env):
    kb_env.add_chunks("shared.pdf", _chunks("shared knowledge text " * 10), "team", "user:1", "h1")
    kb_env.add_chunks("mine.pdf", _chunks("personal note text " * 10), "personal", "user:2", "h2")

    files_u2 = {f["filename"] for f in kb_env.list_files(owner="user:2")}
    assert files_u2 == {"shared.pdf", "mine.pdf"}

    files_u3 = {f["filename"] for f in kb_env.list_files(owner="user:3")}
    assert files_u3 == {"shared.pdf"}

    shared = next(f for f in kb_env.list_files(owner="user:3") if f["filename"] == "shared.pdf")
    assert shared["library"] == "team"
    assert shared["uploader"] == "user:1"


def test_query_chunks_respects_scopes(kb_env):
    kb_env.add_chunks("shared.pdf", _chunks("alpha team topic " * 20), "team", "user:1", "h1")
    kb_env.add_chunks("mine.pdf", _chunks("beta personal topic " * 20), "personal", "user:2", "h2")
    kb_env.add_chunks("other.pdf", _chunks("gamma secret topic " * 20), "personal", "user:3", "h3")

    mine = kb_env.query_chunks("beta", scopes=["kb:user:2"], top_k=5)
    assert mine and all(c["scope"] == "kb:user:2" for c in mine)

    both = kb_env.query_chunks("topic", scopes=["kb:team", "kb:user:2"], top_k=10)
    assert {c["scope"] for c in both} <= {"kb:team", "kb:user:2"}
    assert len(both) >= 2

    other_zone = kb_env.query_chunks("gamma", scopes=["kb:user:2"], top_k=5)
    assert all(c["scope"] == "kb:user:2" for c in other_zone)
    assert all("gamma" not in c["text"] for c in other_zone)


def test_chunk_hash_dedup_and_metadata(kb_env):
    text = "duplicate chunk content " * 10
    first = kb_env.add_chunks("a.pdf", _chunks(text), "team", "user:1", "hash-a")
    assert first == 1
    again = kb_env.add_chunks("a.pdf", _chunks(text), "team", "user:1", "hash-a")
    assert again == 0

    doc = next(iter(kb_env.get_kb_store()._documents.values()))
    assert doc["chunk_hash"] == hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
    assert doc["scope"] == "kb:team"
    assert doc["content_hash"] == "hash-a"
    assert doc["page"] == 1 and doc["para"] == 0
    assert doc["filename"] == "a.pdf" and doc["owner"] == "user:1"


def test_find_by_hash_only_same_zone(kb_env):
    kb_env.add_chunks("a.pdf", _chunks("team content " * 10), "team", "user:1", "samehash")
    assert kb_env.find_by_hash("team", "user:9", "samehash") is not None
    assert kb_env.find_by_hash("personal", "user:1", "samehash") is None


def test_remove_file_only_by_uploader(kb_env):
    kb_env.add_chunks("shared.pdf", _chunks("shared content " * 10), "team", "user:1", "h1")

    assert kb_env.remove_file("shared.pdf", "team", "user:2") == 0
    assert kb_env.remove_file("shared.pdf", "personal", "user:1") == 0
    assert kb_env.remove_file("shared.pdf", "team", "user:1") == 1
    assert kb_env.list_files(owner="user:1") == []


def test_find_chunk_with_neighbors_and_access_control(kb_env):
    kb_env.add_chunks("mine.pdf", _chunks("chunk one " * 10, "chunk two " * 10, "chunk three " * 10),
                      "personal", "user:2", "h2")
    docs = sorted(kb_env.get_kb_store()._documents.values(), key=lambda d: d["chunk_index"])
    middle_hash = docs[1]["chunk_hash"]

    view = kb_env.find_chunk(owner="user:2", filename="mine.pdf", chunk_hash=middle_hash)
    assert view["text"] == docs[1]["text"]
    assert view["before"] == docs[0]["text"]
    assert view["after"] == docs[2]["text"]
    assert view["page"] == 2

    assert kb_env.find_chunk(owner="user:3", filename="mine.pdf", chunk_hash=middle_hash) is None


def test_vector_store_scopes_filter(kb_env):
    store = kb_env.get_kb_store()
    store.add_documents([
        {"text": "team doc", "scope": "kb:team"},
        {"text": "personal doc", "scope": "kb:user:2"},
    ])
    hits = store.search("query", top_k=10, scopes=["kb:team"])
    assert [h["text"] for h in hits] == ["team doc"]
    assert len(store.search("query", top_k=10, scopes=["kb:team", "kb:user:2"])) == 2
