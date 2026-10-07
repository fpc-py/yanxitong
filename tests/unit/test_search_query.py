"""Unit tests for retriever search-query distillation (topic/question → keywords)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.retriever.agent import RetrieverAgent


def _agent_with_llm(response: str | Exception) -> RetrieverAgent:
    agent = RetrieverAgent()
    mock = AsyncMock()
    if isinstance(response, Exception):
        mock.side_effect = response
    else:
        mock.return_value = response
    agent._call_llm = mock  # type: ignore[method-assign]
    return agent


TOPIC = "钙钛矿太阳能电池的稳定性研究"
QUERY = "请检索钙钛矿太阳能电池稳定性近五年的核心文献，总结主要提升策略与待解决的研究争议"


@pytest.mark.asyncio
async def test_distilled_query_first_then_topic_then_raw():
    agent = _agent_with_llm('{"query": "perovskite solar cell stability passivation encapsulation 钙钛矿 稳定性"}')
    candidates = await agent._build_search_candidates(TOPIC, QUERY)
    assert candidates[0] == "perovskite solar cell stability passivation encapsulation 钙钛矿 稳定性"
    assert candidates[1] == TOPIC
    assert candidates[2] == QUERY


@pytest.mark.asyncio
async def test_distillation_failure_falls_back_to_topic_and_query():
    agent = _agent_with_llm(RuntimeError("offline"))
    candidates = await agent._build_search_candidates(TOPIC, QUERY)
    assert candidates == [TOPIC, QUERY]


@pytest.mark.asyncio
async def test_distillation_garbage_is_discarded():
    for garbage in ('{"query": ""}', "not json at all", '{"query": "%s"}' % ("x" * 300)):
        agent = _agent_with_llm(garbage)
        candidates = await agent._build_search_candidates(TOPIC, QUERY)
        assert candidates[0] == TOPIC  # 垃圾输出被丢弃，回退主题
        assert QUERY in candidates


@pytest.mark.asyncio
async def test_generic_topic_skipped_and_deduped():
    agent = _agent_with_llm(RuntimeError("offline"))
    candidates = await agent._build_search_candidates("general", QUERY)
    assert candidates == [QUERY]
    # 蒸馏结果与主题重复时去重，主题 = 问题时不再重复候选
    agent2 = _agent_with_llm(f'{{"query": "{TOPIC}"}}')
    assert await agent2._build_search_candidates(TOPIC, TOPIC) == [TOPIC]


@pytest.mark.asyncio
async def test_execute_impl_retries_with_fallback_candidate():
    """首个候选（蒸馏句）三源全空时，用下一候选（主题）重试一次。"""
    agent = _agent_with_llm('{"query": "distilled en keywords"}')
    calls: list[str] = []

    async def fake_search(query: str):
        calls.append(query)
        if query == "distilled en keywords":
            return [], {"arxiv": 0, "openalex": 0, "semantic_scholar": 0}
        return [{"title": "Perovskite stability", "source": "openalex"}], {"arxiv": 0, "openalex": 1, "semantic_scholar": 0}

    class _StubGraphStore:
        async def papers_existing(self, ids):
            return []

    state = {
        "user_query": QUERY,
        "research_topic": TOPIC,
        "session_id": "s1",
    }
    fake_vs = MagicMock()  # 向量索引在此测试中无关紧要：mock 掉，避免加载真实 BGE
    with patch.object(agent, "_search_all_sources", side_effect=fake_search), \
         patch.object(agent, "_analyze_literature", new=AsyncMock(return_value=([], []))), \
         patch("src.agents.retriever.agent.get_vector_store", return_value=fake_vs), \
         patch("src.agents.retriever.agent.get_graph_store", new=AsyncMock(return_value=_StubGraphStore())), \
         patch("src.agents.retriever.agent.PaperEnricher") as enricher_cls:
        enricher_cls.return_value.enrich_papers = AsyncMock(side_effect=lambda papers, q: papers)
        result = await agent._execute_impl(state)

    assert calls == ["distilled en keywords", TOPIC]
    assert result.success
    assert result.data["count"] == 1
    # 检索结果携带确定性图谱身份（只读关联，不写图）
    link = result.data["kg_links"]["papers"][0]
    assert link["paper_id"].startswith("th:") and link["title_hash"]
    assert result.data["kg_links"]["known_in_graph"] == 0
