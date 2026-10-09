"""三元组幻觉校验器：LLM 抽取 (方法, 数据集, 指标[, 数值]) → 图谱反向查找 → 5 态判定。

判定矩阵（图盲区不惩罚）：

    verified         1.0  方法/数据集/指标均在图谱且有论文同时支撑该组合
    partial          0.6  方法与数据集在，指标实体缺失
    unknown          0.5  关键实体不在图谱（无法判断，不视为幻觉）
    contradicted     0.2  两实体都存在，但没有任何论文同时支撑该组合
    numeric_conflict 0.1  陈述数值与图谱记录的同一指标值冲突

图谱查询由 :meth:`GraphStore.verify_triples` 完成，本模块只负责抽取与打分，
两者分离使判定逻辑可用假数据做纯函数测试。
"""

import logging
import re
from collections import Counter
from typing import Optional

from src.agents.base import BaseAgent, parse_llm_json
from src.knowledge.graph_store import get_graph_store

logger = logging.getLogger(__name__)

MAX_TRIPLES = 8
VALUE_TOLERANCE = 0.05

STATE_SCORES = {
    "verified": 1.0,
    "partial": 0.6,
    "unknown": 0.5,
    "contradicted": 0.2,
    "numeric_conflict": 0.1,
}

HARD_CONFLICT_STATES = ("contradicted", "numeric_conflict")

EXTRACT_PROMPT = """Extract empirical claims from the answer below as a JSON object.
A claim qualifies only when it names a method, a dataset and a metric; include the
numeric value when one is stated. Copy wording verbatim into "quote".
Return JSON: {{"triples": [{{"method": "...", "dataset": "...", "metric": "...", "value": "...", "quote": "..."}}]}}
Return an empty list when nothing qualifies. Do not invent claims.

Answer:
{answer}"""


def _as_float(text) -> Optional[float]:
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(m.group(0)) if m else None


def values_conflict(claimed, recorded) -> bool:
    """True when both parse as numbers and differ beyond ``VALUE_TOLERANCE``."""
    a, b = _as_float(claimed), _as_float(recorded)
    if a is None or b is None:
        return False
    return abs(a - b) > max(0.005, VALUE_TOLERANCE * abs(b))


def score_triple(fact: dict) -> dict:
    """5 态判定：把 ``verify_triples`` 返回的一条图谱事实打成状态 + 分数。"""
    triple = fact.get("triple") or {}
    method = fact.get("method") or {}
    dataset = fact.get("dataset") or {}
    metric = fact.get("metric") or {}
    papers = fact.get("supporting_papers") or []

    if not method.get("entity_id") or not dataset.get("entity_id"):
        state = "unknown"
        reason = "图谱中没有对应方法/数据集实体（盲区不惩罚）"
    elif not papers:
        state = "contradicted"
        reason = "方法/数据集均存在，但没有任何论文同时支撑该组合"
    elif triple.get("metric") and not metric.get("entity_id"):
        state = "partial"
        reason = "指标实体不在图谱"
    else:
        conflicts = [p for p in papers if values_conflict(triple.get("value"), p.get("value"))]
        if triple.get("value") and conflicts:
            state = "numeric_conflict"
            reason = f"数值 {triple['value']} 与图谱记录 {conflicts[0].get('value')} 冲突"
        else:
            state = "verified"
            reason = "图谱支撑"

    return {
        "method": triple.get("method", ""),
        "dataset": triple.get("dataset", ""),
        "metric": triple.get("metric", ""),
        "value": triple.get("value", ""),
        "quote": triple.get("quote", ""),
        "state": state,
        "score": STATE_SCORES[state],
        "reason": reason,
        "supporting_papers": [
            {"paper_id": p.get("paper_id", ""), "title": p.get("title", "")}
            for p in papers[:3]
        ],
    }


class _ExtractorAgent(BaseAgent):
    """仅借 BaseAgent 的 LLM 调用链（语义缓存/熔断），不参与编排。"""

    name = "triple_extractor"
    description = "三元组抽取（lightweight LLM）"
    model_role = "lightweight"

    async def _execute_impl(self, state: dict):
        raise NotImplementedError("triple_extractor is prompt-driven only")


class TripleVerifier:
    """规格③核心：把答案中的经验性断言拿回图谱反查。"""

    def __init__(self, agent: Optional[BaseAgent] = None) -> None:
        self._agent = agent

    def _get_agent(self) -> BaseAgent:
        if self._agent is None:
            self._agent = _ExtractorAgent()
        return self._agent

    async def extract_triples(self, answer: str) -> list[dict]:
        """LLM 抽取，封闭 schema；任何失败返回空列表（降级不阻塞回答）。"""
        if not (answer or "").strip():
            return []
        try:
            raw = parse_llm_json(
                await self._get_agent()._call_llm(
                    EXTRACT_PROMPT.format(answer=answer[:4000]), json_mode=True
                )
            )
        except Exception as exc:
            logger.warning("三元组抽取降级: %s", exc)
            return []
        out: list[dict] = []
        for item in (raw.get("triples") or []):
            if len(out) >= MAX_TRIPLES:
                break
            if not isinstance(item, dict):
                continue
            method = str(item.get("method") or "").strip()
            dataset = str(item.get("dataset") or "").strip()
            if not method or not dataset:
                continue
            out.append({
                "method": method,
                "dataset": dataset,
                "metric": str(item.get("metric") or "").strip(),
                "value": str(item.get("value") or "").strip(),
                "quote": str(item.get("quote") or "")[:200],
            })
        return out

    async def verify_answer(self, answer: str, kg_query=None, kb_id: str = "") -> dict:
        """抽取 + 反查 + 判定，返回可序列化的报告 dict。

        ``kg_query`` 可注入替身（测试）；缺省用全局 GraphStore。
        ``kb_id`` 把反查限定在领域包内（空 = 旧全局行为）。
        """
        triples = await self.extract_triples(answer)
        if not triples:
            return {"checked": 0, "score": 0.5, "triples": [], "flags": [],
                    "states": {}, "degraded": False}
        try:
            gs = kg_query if kg_query is not None else await get_graph_store()
            verify_kwargs = {"kb_id": kb_id} if kb_id else {}
            facts = await gs.verify_triples(triples, **verify_kwargs)
        except Exception as exc:
            logger.warning("三元组反查降级: %s", exc)
            return {"checked": len(triples), "score": 0.5, "triples": [], "flags": [],
                    "states": {}, "degraded": True}
        results = [score_triple(fact) for fact in facts]
        states = Counter(r["state"] for r in results)
        score = sum(r["score"] for r in results) / len(results) if results else 0.5
        return {
            "checked": len(results),
            "score": round(score, 3),
            "triples": results,
            "flags": [r for r in results if r["state"] in HARD_CONFLICT_STATES],
            "states": dict(states),
            "degraded": False,
        }


_verifier: Optional[TripleVerifier] = None


def get_triple_verifier() -> TripleVerifier:
    global _verifier
    if _verifier is None:
        _verifier = TripleVerifier()
    return _verifier
