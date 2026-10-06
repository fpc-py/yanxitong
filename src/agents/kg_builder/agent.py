"""KG Builder Agent - 知识图谱构建智能体."""

import asyncio, json, logging
from src.agents.base import BaseAgent, AgentResult
from src.knowledge.graph_store import get_graph_store

logger = logging.getLogger(__name__)

# 注意：提示词里的字面量 JSON 花括号必须转义成 {{ }}，否则会被 str.format() 当作
# 占位符而抛 KeyError（历史 bug：导致 KG 抽取每次都失败、Neo4j 始终为空）。
ENTITY_PROMPT = """Extract entities and relations from the paper abstract.
Return JSON: {{"entities":[{{"id":"uid","type":"Method|Dataset|Metric|Model|Theory|Finding|Tool|Field","name":"name","properties":{{}}}}],"relations":[{{"source_id":"uid","target_id":"uid","type":"PROPOSES|EVALUATES|OUTPERFORMS|USES_DATASET|IMPROVES|COMPARES_WITH|BELONGS_TO"}}]}}
Paper: {title}
Abstract: {abstract}"""

# KG 抽取的规模控制：每篇论文一次 LLM 调用，而检索一次最多返回 20 篇。
# 全量 20 篇会把单次问答拖到 3 分钟以上（上游按 key 排队，客户端并发收效有限），
# 因此默认只对最相关的若干篇建图——覆盖度与响应延迟的折中。
MAX_CONCURRENCY = 5
MAX_PAPERS = 4
# 送入抽取的摘要长度上限（越长调用越慢）
ABSTRACT_LIMIT = 1200


class KGBuilderAgent(BaseAgent):
    name = "kg_builder"
    description = "知识图谱构建智能体"
    model_role = "lightweight"

    @staticmethod
    def _parse_json(resp: str) -> dict:
        """解析模型返回的 JSON；容忍代码围栏与前后夹杂的说明文字。"""
        resp = resp.strip()
        if resp.startswith("```"):
            resp = resp.split("\n", 1)[-1]
            if resp.endswith("```"):
                resp = resp[:-3]
            resp = resp.strip()
        try:
            data = json.loads(resp)
        except json.JSONDecodeError:
            # 模型偶尔在 JSON 前后附带一句话，截取首个 { 到最后一个 } 再解析
            start, end = resp.find("{"), resp.rfind("}")
            if start == -1 or end <= start:
                raise
            data = json.loads(resp[start:end + 1])
        return data if isinstance(data, dict) else {}

    async def _extract_one(self, paper: dict, sem: asyncio.Semaphore) -> dict:
        """抽取单篇论文的实体/关系；无有效摘要时返回空结果。"""
        async with sem:
            title = paper.get("title", "Untitled")
            abstract = paper.get("abstract", "")[:ABSTRACT_LIMIT]
            if not (abstract and len(abstract) >= 50):
                return {}
            prompt = ENTITY_PROMPT.format(title=title, abstract=abstract)
            # 开启 JSON 模式，避免模型在 JSON 前后夹带说明文字导致解析失败
            return self._parse_json(await self._call_llm(prompt, json_mode=True))

    async def _execute_impl(self, state: dict) -> AgentResult:
        papers = state.get("literature_results", [])
        if not papers:
            return AgentResult(success=False, error="No papers in literature_results", confidence=0.0)
        self._audit("kg_start", {"paper_count": len(papers)})

        # 并发抽取：原先逐篇串行 await，20 篇会放大成 20 次串行 LLM 往返，
        # 是整条问答链路的主要耗时来源。改为受信号量约束的并发。
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        targets = papers[:MAX_PAPERS]
        results = await asyncio.gather(
            *(self._extract_one(p, sem) for p in targets), return_exceptions=True
        )

        all_entities, all_relations, processed = [], [], 0
        for res in results:
            if isinstance(res, Exception):
                logger.warning("KG extraction failed for paper: %s", res)
                continue
            if not res:
                continue
            all_entities.extend(res.get("entities", []))
            all_relations.extend(res.get("relations", []))
            processed += 1

        # 图谱按「研究问题（会话）」隔离：实体/关系 id 加会话前缀后写入，
        # 不同会话抽取的实体互不合并、检索时按前缀过滤，避免跨会话串图。
        scope = state.get("session_id", "")
        if scope:
            all_entities = [{**e, "id": f"{scope}|{e.get('id', '')}"} for e in all_entities]
            all_relations = [
                {**r,
                 "source_id": f"{scope}|{r.get('source_id', '')}",
                 "target_id": f"{scope}|{r.get('target_id', '')}"}
                for r in all_relations
            ]

        self._audit("kg_done", {"processed": processed, "entities": len(all_entities), "relations": len(all_relations)})
        try:
            gs = await get_graph_store()
            if all_entities:
                await gs.create_entities(all_entities)
            if all_relations:
                await gs.create_relations(all_relations)
        except Exception as e:
            logger.warning("Neo4j storage degraded: %s", e)
        # 置信度按「实际尝试抽取的篇数」归一（检索可能返回 20 篇，但只对前 MAX_PAPERS 篇建图）
        attempted = max(1, min(len(papers), MAX_PAPERS))
        return AgentResult(success=True, data={"processed": processed, "entities": len(all_entities), "relations": len(all_relations)}, confidence=min(1.0, processed/attempted))