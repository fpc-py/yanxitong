"""Unit tests for citation-chain evidence matching, including CJK KB sources."""

from src.safety.citation import CitationTracker

KB_DOC = {
    "title": "[KB] 联邦学习近端项笔记.txt",
    "text": "本文提出 FedProx 方法，通过在本地目标函数中加入近端项来约束客户端更新，缓解非独立同分布数据下的漂移问题。",
    "kind": "knowledge",
    "filename": "联邦学习近端项笔记.txt",
    "page": 1,
    "chunk_hash": "abc123",
    "url": "",
    "authors": [],
    "year": 0,
}


def test_english_claim_matches_paper_doc():
    docs = [{"title": "FedProx Study", "abstract": "We propose a proximal term for federated optimization.", "url": "u", "year": 2020}]
    chain = CitationTracker.build_chain("We propose a proximal term for federated optimization.", docs)
    assert chain.claims
    assert chain.claims[0].kind == "paper"
    assert chain.claims[0].source_title == "FedProx Study"


def test_cjk_claim_anchors_to_kb_chunk():
    claim = "具体来说，近端项的作用是限制每个客户端的模型参数更新不要偏离全局模型太远，从而减少模型漂移现象。"
    chain = CitationTracker.build_chain(claim, [KB_DOC])
    assert len(chain.claims) == 1
    evidence = chain.claims[0]
    assert evidence.kind == "knowledge"
    assert evidence.filename == "联邦学习近端项笔记.txt"
    assert evidence.chunk_hash == "abc123"
    assert evidence.page == "1"
    assert "近端项" in evidence.excerpt


def test_kb_claim_beats_unrelated_paper():
    unrelated_paper = {"title": "Cooking with Graphs", "abstract": "A survey of cuisine networks.", "year": 1999}
    claim = "近端项约束客户端更新，缓解非独立同分布数据下的漂移问题。"
    chain = CitationTracker.build_chain(claim, [unrelated_paper, KB_DOC])
    assert chain.claims[0].kind == "knowledge"


def test_chain_to_dict_exposes_kb_fields():
    claim = "近端项约束客户端更新，缓解非独立同分布数据下的漂移问题。"
    chain = CitationTracker.build_chain(claim, [KB_DOC])
    payload = chain.to_dict()
    source = payload["claims"][0]["source"]
    assert source["kind"] == "knowledge"
    assert source["filename"] == "联邦学习近端项笔记.txt"
    assert source["chunk_hash"] == "abc123"
    assert payload["claims"][0]["evidence"]["page"] == "1"
