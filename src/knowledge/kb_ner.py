"""上传文档的轻量实体抽取：前若干块 → LLM → Neo4j（不污染会话图谱）。

复用 kg_builder 的 ENTITY_PROMPT 与 LLM 调用链（语义缓存/熔断）；实体 id
加库前缀（``kb:team|`` / ``kb:{owner}|``），与论文会话图谱（``{session_id}|``）
彻底隔离。任何失败只降级记录，不影响上传主流程。
"""

import asyncio
import logging

from src.agents.base import parse_llm_json
from src.agents.kg_builder.agent import ENTITY_PROMPT, KGBuilderAgent
from src.core.config import get_settings
from src.knowledge.graph_store import get_graph_store

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 3
TEXT_LIMIT = 1200

_agent: KGBuilderAgent | None = None


def _get_agent() -> KGBuilderAgent:
    global _agent
    if _agent is None:
        _agent = KGBuilderAgent()
    return _agent


def kb_prefix(library: str, owner: str) -> str:
    return "kb:team" if library == "team" else f"kb:{owner}"


async def _extract_one(chunk: dict, filename: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        text = (chunk.get("text") or "")[:TEXT_LIMIT]
        if len(text) < 50:
            return {}
        prompt = ENTITY_PROMPT.format(title=filename, abstract=text)
        try:
            return parse_llm_json(await _get_agent()._call_llm(prompt, json_mode=True))
        except Exception as exc:
            logger.warning("KB NER 抽取失败: %s", exc)
            return {}


async def index_chunks(chunks: list[dict], library: str, owner: str, filename: str) -> dict:
    """对前 ``kb.ner_max_chunks`` 个块抽实体/关系并写入 Neo4j（前缀隔离）。"""
    targets = chunks[: get_settings().kb.ner_max_chunks]
    if not targets:
        return {"entities": 0, "relations": 0}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(_extract_one(ch, filename, sem) for ch in targets), return_exceptions=True
    )
    entities: list[dict] = []
    relations: list[dict] = []
    for res in results:
        if isinstance(res, Exception) or not res:
            continue
        entities.extend(res.get("entities", []))
        relations.extend(res.get("relations", []))

    prefix = kb_prefix(library, owner)
    entities = [{**e, "id": f"{prefix}|{e.get('id', '')}"} for e in entities if e.get("id")]
    relations = [
        {**r,
         "source_id": f"{prefix}|{r.get('source_id', '')}",
         "target_id": f"{prefix}|{r.get('target_id', '')}"}
        for r in relations
    ]
    if not entities:
        return {"entities": 0, "relations": 0}
    try:
        gs = await get_graph_store()
        await gs.create_entities(entities)
        if relations:
            await gs.create_relations(relations)
    except Exception as exc:
        logger.warning("KB 图谱写入降级: %s", exc)
        return {"entities": 0, "relations": 0, "degraded": True}
    return {"entities": len(entities), "relations": len(relations)}
