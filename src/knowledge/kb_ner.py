"""上传文档的轻量实体抽取：前若干块 → LLM → Neo4j（不污染论文图谱）。

复用 kg_builder 的 schema 封闭约束（提示词由配置生成、写入前代码级 gate）；
实体 id 加库前缀（``kb:team|`` / ``kb:{owner}|``），与论文事实图谱
（全局累积）保持隔离。任何失败只降级记录，不影响上传主流程。
"""

import asyncio
import logging

from src.agents.base import parse_llm_json
from src.agents.kg_builder.agent import KGBuilderAgent, entity_prompt_template, schema_entity_type, schema_relation_type
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
        prompt = entity_prompt_template().format(title=filename, abstract=text)
        try:
            raw = parse_llm_json(await _get_agent()._call_llm(prompt, json_mode=True))
        except Exception as exc:
            logger.warning("KB NER 抽取失败: %s", exc)
            return {}
        return _gate(raw)


def _gate(raw: dict) -> dict:
    """把 LLM 输出收敛到封闭 schema（本地 uid 保留，供关系端点引用）。"""
    entities, valid_uids = [], set()
    for ent in raw.get("entities") or []:
        etype = schema_entity_type(ent.get("type"))
        name = str(ent.get("name") or "").strip()
        if not etype or not name or ent.get("id") is None:
            continue
        valid_uids.add(str(ent.get("id")))
        entities.append({"id": str(ent.get("id")), "type": etype, "name": name})
    relations = []
    for rel in raw.get("relations") or []:
        rtype = schema_relation_type(rel.get("type"))
        if not rtype or str(rel.get("source_id")) not in valid_uids or str(rel.get("target_id")) not in valid_uids:
            continue
        relations.append({"source_id": str(rel.get("source_id")), "target_id": str(rel.get("target_id")), "type": rtype})
    return {"entities": entities, "relations": relations}


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
