"""Unit tests for the triple hallucination checker (5-state matrix + integration)."""

import json

from src.safety import triple_check as tc
from src.safety.hallucination import HallucinationDefense

TRIPLE = {"method": "FedProx", "dataset": "FEMNIST", "metric": "Accuracy", "value": ""}


def _fact(paper_values=None, mid="e:m1", did="e:d1", mtid="e:t1", triple=None, papers=True):
    return {
        "triple": dict(triple or TRIPLE),
        "method": {"entity_id": mid, "name": "FedProx"} if mid else {},
        "dataset": {"entity_id": did, "name": "FEMNIST"} if did else {},
        "metric": {"entity_id": mtid, "name": "Accuracy"} if mtid else {},
        "supporting_papers": (
            [{"paper_id": "ax:1812.06127", "title": "FedProx", "value": v} for v in paper_values]
            if papers and paper_values is not None
            else ([{"paper_id": "ax:1812.06127", "title": "FedProx"}] if papers else [])
        ),
    }


class _FakeAgent:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.prompts = []

    async def _call_llm(self, prompt, json_mode=False, **kwargs):
        self.prompts.append(prompt)
        if self.exc:
            raise self.exc
        return json.dumps(self.payload)


class _FakeStore:
    def __init__(self, facts_by_method=None, exc=None):
        self.facts_by_method = facts_by_method or {}
        self.exc = exc

    async def verify_triples(self, triples):
        if self.exc:
            raise self.exc
        return [self.facts_by_method[t["method"]] for t in triples]


# ---- 5 态判定（纯函数） -------------------------------------------------------

def test_state_verified_when_value_matches():
    result = tc.score_triple(_fact(paper_values=["4.1"], triple={**TRIPLE, "value": "4.1%"}))
    assert result["state"] == "verified" and result["score"] == 1.0


def test_state_partial_when_metric_missing():
    result = tc.score_triple(_fact(paper_values=["4.1"], mtid=None))
    assert result["state"] == "partial" and result["score"] == 0.6


def test_state_unknown_blind_spot_not_penalised():
    result = tc.score_triple(_fact(mid=None))
    assert result["state"] == "unknown" and result["score"] == 0.5


def test_state_contradicted_when_no_joint_paper():
    result = tc.score_triple(_fact(papers=False))
    assert result["state"] == "contradicted" and result["score"] == 0.2


def test_state_numeric_conflict():
    result = tc.score_triple(_fact(paper_values=["4.1"], triple={**TRIPLE, "value": "3.2"}))
    assert result["state"] == "numeric_conflict" and result["score"] == 0.1
    assert "4.1" in result["reason"]


def test_values_conflict_tolerance():
    assert not tc.values_conflict("4.1", "4.15")   # 1.2% 相对误差内
    assert tc.values_conflict("4.1", "3.2")
    assert not tc.values_conflict("", "4.1")       # 无数值即无法冲突
    assert not tc.values_conflict("4.1", None)


# ---- verify_answer 端到端（假抽取器 + 假图谱） ---------------------------------

async def test_verify_answer_full_report():
    payload = {"triples": [
        {"method": "FedProx", "dataset": "FEMNIST", "metric": "Accuracy", "value": "4.1"},
        {"method": "BAD", "dataset": "FEMNIST", "metric": "Accuracy", "value": "9"},
        {"method": "NOVEL", "dataset": "FEMNIST", "metric": "Accuracy", "value": "1"},
    ]}
    verifier = tc.TripleVerifier(agent=_FakeAgent(payload))
    store = _FakeStore({
        "FedProx": _fact(paper_values=["4.1"]),
        "BAD": _fact(paper_values=["4.1"], triple={**TRIPLE, "method": "BAD", "value": "9"}),
        "NOVEL": _fact(mid=None, triple={**TRIPLE, "method": "NOVEL"}),
    })

    report = await verifier.verify_answer("Some answer text " * 5, kg_query=store)

    assert report["checked"] == 3
    assert report["states"] == {"verified": 1, "numeric_conflict": 1, "unknown": 1}
    assert [f["state"] for f in report["flags"]] == ["numeric_conflict"]
    assert report["degraded"] is False
    assert report["score"] == round((1.0 + 0.1 + 0.5) / 3, 3)


async def test_extract_filters_incomplete_and_caps():
    payload = {"triples": [
        {"method": "A", "dataset": "B", "metric": "C"},
        {"method": "无数据集", "metric": "C"},  # 丢弃：缺 dataset
        *[{"method": f"M{i}", "dataset": "D", "metric": "C"} for i in range(12)],
    ]}
    verifier = tc.TripleVerifier(agent=_FakeAgent(payload))
    triples = await verifier.extract_triples("answer")
    assert len(triples) == tc.MAX_TRIPLES  # 12+1 → 截断到 8
    assert all(t["dataset"] == "D" for t in triples[1:])


async def test_extraction_failure_degrades_neutral():
    verifier = tc.TripleVerifier(agent=_FakeAgent(exc=RuntimeError("llm down")))
    report = await verifier.verify_answer("answer", kg_query=_FakeStore())
    assert report == {"checked": 0, "score": 0.5, "triples": [], "flags": [],
                      "states": {}, "degraded": False}


async def test_store_failure_degrades_neutral():
    verifier = tc.TripleVerifier(agent=_FakeAgent({"triples": [TRIPLE]}))
    report = await verifier.verify_answer("answer", kg_query=_FakeStore(exc=RuntimeError("neo4j down")))
    assert report["degraded"] is True and report["checked"] == 1
    assert report["score"] == 0.5 and report["flags"] == []


# ---- 与六层防线集成 -----------------------------------------------------------

async def test_defense_escalates_on_triple_conflict():
    defense = HallucinationDefense()
    report = {
        "checked": 1, "score": 0.1, "degraded": False,
        "states": {"numeric_conflict": 1},
        "flags": [{"state": "numeric_conflict", "score": 0.1, "method": "FedProx",
                   "dataset": "FEMNIST", "metric": "Accuracy", "reason": "数值 3.2 与图谱记录 4.1 冲突"}],
    }
    out = await defense.evaluate("FedProx 在 FEMNIST 上达到 3.2% 准确率。", [], [], triple_report=report)

    assert out.layer_results["kg_verification"].score == 0.1
    assert "numeric_conflict=1" in out.layer_results["kg_verification"].details
    assert out.risk_level == "high" and out.requires_human_review is True
    assert any(f["layer"] == "kg_verification" and "冲突" in f["details"] for f in out.flagged_claims)


async def test_defense_without_triple_report_unchanged():
    defense = HallucinationDefense()
    out = await defense.evaluate("Some grounded claim about federated learning.", [], [])
    assert out.layer_results["kg_verification"].score == 0.5
    assert out.layer_results["kg_verification"].details == "知识图谱不可用"


async def test_supervisor_records_hard_conflicts_to_audit(monkeypatch, tmp_path):
    """硬冲突 → supervisor 把 triple_check + hallucination_defense 标记写入 audit.db。"""
    from unittest.mock import AsyncMock

    from src.observability import audit_store

    monkeypatch.setattr(audit_store, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(audit_store, "_conn", None)
    try:
        from src.agents.supervisor import agent as sup

        class _FakeGraphRAG:
            async def query(self, query, scope=None, user_id=""):
                return {"fused_context": "ctx", "citations": [], "kb_docs": [], "kg_entities": []}

        class _FakeVerifier:
            async def verify_answer(self, answer):
                return {
                    "checked": 1, "score": 0.1, "degraded": False,
                    "states": {"numeric_conflict": 1}, "triples": [],
                    "flags": [{"state": "numeric_conflict", "score": 0.1, "method": "FedProx",
                               "dataset": "FEMNIST", "metric": "Accuracy", "reason": "值冲突"}],
                }

        monkeypatch.setattr(sup, "get_graphrag", AsyncMock(return_value=_FakeGraphRAG()))
        monkeypatch.setattr(tc, "get_triple_verifier", lambda: _FakeVerifier())

        agent = sup.SupervisorAgent()

        async def fake_llm(prompt, **kwargs):
            if "intent" in prompt.lower():
                return '{"intent":"comprehensive","confidence":0.9}'
            return "FedProx 在 FEMNIST 上达到 3.2% 准确率。"

        agent._call_llm = fake_llm  # type: ignore[method-assign]

        result = await agent.execute({
            "user_query": "差分隐私进展",
            "session_id": "s-flag",
            "literature_results": [{"title": "p1", "abstract": "a" * 50}],
        })

        assert result.success and result.data["triple_report"]["flags"]
        flags = audit_store.recent_flags("s-flag")
        assert {f["layer"] for f in flags} == {"triple_check", "hallucination_defense"}
        defense_flag = [f for f in flags if f["layer"] == "hallucination_defense"][0]
        assert defense_flag["risk_level"] == "high"
    finally:
        if audit_store._conn is not None:
            audit_store._conn.close()
        audit_store._conn = None
