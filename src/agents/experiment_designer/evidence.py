"""规格②证据检索：KG 路径（Task→Method→Dataset→Metric）+ 内置知识库 + 文献锚点。

``collect_evidence`` 是全管道唯一的检索入口：Neo4j 不可用、KB 向量通道不可用时
都只降级（``degraded`` + 原因），绝不抛异常中断设计流程。所有候选/诊断必须引用
本模块产出的 anchor id（``kg:ent:*`` / ``kg:chain:*`` / ``kb:*`` / ``paper:*``），
引用不到的证据一律剔除——这是规格⑪"无引用不建议"的数据基础。
"""

from __future__ import annotations

import logging
import re

from src.core.config import get_settings
from src.knowledge import knowledge_libs
from src.knowledge.graph_store import get_graph_store
from src.tools.paper_schema import make_paper_id

logger = logging.getLogger(__name__)

MAX_KEYWORDS = 12
MAX_ENTITIES = 60
MAX_CHAINS = 20
MAX_PATHS = 60
MAX_PAPERS = 10

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_STOP_TOKENS = {
    "the", "and", "for", "with", "from", "using", "based", "model", "method",
    "task", "data", "experiment", "analysis", "研究", "实验", "方法", "模型",
    "数据", "分析", "方案", "设计", "优化",
}

#: 配置里值得作为 KG 检索词的键（值形如模型名/任务名/数据集名）
_CONFIG_KEYS = (
    "model", "model_name", "architecture", "task", "dataset", "datasets",
    "method", "methods", "backbone", "optimizer", "scheduler", "framework",
)


def extract_keywords(intent: str, config: dict | None = None) -> list[str]:
    """从用户意图 + 实验配置中提取 KG 检索关键词（去重、有界）。"""
    text_parts = [str(intent or "")]
    for key in _CONFIG_KEYS:
        if isinstance(config, dict) and config.get(key):
            value = config[key]
            if isinstance(value, (list, tuple)):
                text_parts.extend(str(v) for v in value[:6])
            elif isinstance(value, dict):
                text_parts.extend(str(v) for v in list(value.values())[:6] if v)
            else:
                text_parts.append(str(value))

    text = " ".join(text_parts)
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text):
        low = token.lower()
        if low not in _STOP_TOKENS and token not in tokens:
            tokens.append(token)
    for chunk in _CJK_RE.findall(text):
        if chunk not in tokens and chunk not in _STOP_TOKENS:
            tokens.append(chunk)
    return tokens[:MAX_KEYWORDS]


def _paper_anchors(papers: list[dict] | None, kb_id: str = "") -> list[dict]:
    anchors = []
    for i, paper in enumerate((papers or [])[:MAX_PAPERS]):
        pid = make_paper_id(paper or {}, kb_id)
        if not pid:
            continue
        findings = paper.get("key_findings") or []
        if not findings:
            abstract = str(paper.get("abstract") or "").strip()
            findings = [abstract[:200]] if abstract else []
        anchors.append({
            "id": f"paper:{pid}",
            "kind": "paper",
            "ref": pid,
            "index": i + 1,
            "title": paper.get("title") or "(无标题)",
            "year": paper.get("year") or "",
            "arxiv_id": paper.get("arxiv_id") or "",
            "text": "; ".join(str(f) for f in findings[:3])[:300] or "无明确发现",
            "methods": [str(m) for m in (paper.get("methods") or [])[:5]],
        })
    return anchors


def _kg_anchors(entities: list[dict], chains: list[dict], paths: list[dict]) -> list[dict]:
    anchors: list[dict] = []
    for ent in entities[:MAX_ENTITIES]:
        eid = ent.get("entity_id") or ""
        if not eid:
            continue
        anchors.append({
            "id": f"kg:ent:{eid}",
            "kind": "kg-entity",
            "ref": eid,
            "name": ent.get("name") or eid,
            "type": ent.get("type") or "",
            "text": f"{ent.get('name') or eid}（{ent.get('type') or 'Entity'}）",
        })
    for chain in chains[:MAX_CHAINS]:
        pid = chain.get("paper_id") or ""
        if not pid:
            continue
        parts = []
        if chain.get("methods"):
            parts.append("方法: " + ", ".join(chain["methods"][:4]))
        if chain.get("datasets"):
            parts.append("数据集: " + ", ".join(
                f"{d.get('name')}(n={d.get('n')})" if d.get("n") else str(d.get("name"))
                for d in chain["datasets"][:3]
            ))
        if chain.get("metrics"):
            parts.append("指标: " + ", ".join(
                f"{m.get('name')}={m.get('value')}{m.get('unit') or ''}" if m.get("value") is not None
                else str(m.get("name"))
                for m in chain["metrics"][:3]
            ))
        anchors.append({
            "id": f"kg:chain:{pid}",
            "kind": "kg-chain",
            "ref": pid,
            "title": chain.get("paper_title") or pid,
            "year": chain.get("year") or "",
            "text": "；".join(parts) or "无方法/数据集/指标记录",
        })
    for path in paths[:20]:
        edges = path.get("edges") or []
        if not edges:
            continue
        text = " → ".join(f"{e.get('source')}-[{e.get('type')}]->{e.get('target')}" for e in edges[:4])
        evidence = next((e.get("evidence") for e in edges if e.get("evidence")), "")
        anchors.append({
            "id": f"kg:path:{edges[0].get('source')}:{edges[-1].get('target')}",
            "kind": "kg-path",
            "ref": edges[0].get("source", ""),
            "text": text + (f"（证据: {str(evidence)[:120]}）" if evidence else ""),
        })
    return anchors


def _kb_anchors(kb_result: dict) -> list[dict]:
    anchors = []
    for lib, info in (kb_result.get("libraries") or {}).items():
        for i, chunk in enumerate(info.get("chunks") or []):
            source = chunk.get("source") or f"{lib}/{i}"
            anchors.append({
                "id": f"kb:{lib}:{i}",
                "kind": "kb",
                "ref": source,
                "library": lib,
                "label": info.get("label") or lib,
                "section": chunk.get("section") or "",
                "similarity": chunk.get("similarity", 0.0),
                "text": (chunk.get("text") or "")[:400],
            })
    return anchors


async def collect_evidence(
    intent: str,
    config: dict | None = None,
    session_id: str = "",
    papers: list[dict] | None = None,
    kb_id: str = "",
    cross_kb_ids: list[str] | None = None,
) -> dict:
    """聚合 KG / 内置知识库 / 文献三类证据，任一来源失败只降级不中断。

    ``kb_id`` 给定后图谱证据按领域包收窄：会话视图（精确锚点）+ 本包全局
    （跨会话复用），显式声明的跨域包以 0.85 权重并入、路径只走主包 + 声明包；
    文献锚点 id 与图谱写入端同用 kb 前缀。

    Returns:
        ``{kg: {entities, chains, paths, degraded, error}, kb: {…recall 结果},
        literature: [...], anchors: [...], degraded, sources}``
    """
    settings = get_settings()
    keywords = extract_keywords(intent, config)
    literature = _paper_anchors(papers, kb_id)

    kg: dict = {"entities": [], "chains": [], "paths": [], "degraded": False, "error": ""}
    paper_ids = [a["ref"] for a in literature]
    if keywords or paper_ids:
        try:
            store = await get_graph_store()
            if keywords:
                ent_kwargs: dict = {"scope": session_id or None}
                if kb_id:
                    ent_kwargs["kb_id"] = kb_id
                kg["entities"] = await store.search_entities_multi(
                    keywords, limit=MAX_ENTITIES, **ent_kwargs
                )
                if kb_id:
                    # 本包全局（跨会话复用）+ 显式跨域（0.85 加权），主包命中优先
                    seen = {e.get("entity_id") for e in kg["entities"]}
                    extra = await store.search_entities_multi(
                        keywords, limit=MAX_ENTITIES,
                        scope=None, kb_id=kb_id, cross_kb_ids=cross_kb_ids or None,
                    )
                    kg["entities"] += [e for e in extra if e.get("entity_id") not in seen]
                    kg["entities"] = kg["entities"][:MAX_ENTITIES]
            if paper_ids:
                kg["chains"] = await store.chain_query(paper_ids, limit=MAX_CHAINS)
            entry_ids = [e.get("entity_id") for e in kg["entities"][:10] if e.get("entity_id")]
            if entry_ids:
                hop_kwargs = {"kb_ids": [kb_id, *(cross_kb_ids or [])]} if kb_id else {}
                expanded = await store.multi_hop_paths(entry_ids, hops=2, limit=MAX_PATHS, **hop_kwargs)
                kg["paths"] = expanded.get("paths") or []
        except Exception as exc:  # Neo4j 不可用：降级为仅 KB + 文献证据
            kg["degraded"] = True
            kg["error"] = str(exc)[:200]
            kg["entities"], kg["chains"], kg["paths"] = [], [], []
            logger.warning("design evidence: KG 检索降级: %s", exc)

    kb_result: dict = {"libraries": {}, "degraded": True, "sources": 0}
    try:
        kb_kwargs = {"kb_id": kb_id} if kb_id else {}
        kb_result = knowledge_libs.recall(
            intent,
            None,
            top_k=settings.designer.recall_top_k,
            libraries=knowledge_libs.DESIGN_LIBRARIES,
            **kb_kwargs,
        )
    except Exception as exc:
        logger.warning("design evidence: 知识库召回降级: %s", exc)

    anchors = (
        _kg_anchors(kg["entities"], kg["chains"], kg["paths"])
        + _kb_anchors(kb_result)
        + literature
    )
    return {
        "keywords": keywords,
        "kg": kg,
        "kb": kb_result,
        "literature": literature,
        "anchors": anchors,
        "degraded": bool(kg["degraded"] or kb_result.get("degraded")),
        "sources": (
            len(kg["entities"]) + len(kg["chains"]) + len(kg["paths"])
            + int(kb_result.get("sources", 0)) + len(literature)
        ),
    }
