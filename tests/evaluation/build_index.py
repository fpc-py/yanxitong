"""A5 收尾：为金标集建一个覆盖主题的小索引。

对代表性 query 触发 RetrieverAgent（它会调 arXiv/S2/OpenAlex API + LLM enrich
+ BGE-M3 编码 FAISS），建完索引后再跑 run_baseline 才测得到真 RAG 质量。

Usage: python -m tests.evaluation.build_index
"""
from __future__ import annotations
import asyncio, logging, sys, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "urllib3", "neo4j", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# 金标集代表性主题（英文为主，arXiv/S2 英文源）
SEED_QUERIES = [
    "BERT bidirectional encoder representations transformers pretraining masked language model",
    "GraphRAG graph retrieval augmented generation knowledge graph",
    "FAISS efficient similarity search product quantization approximate nearest neighbor",
    "BGE-M3 multilingual embedding sparse dense retrieval",
    "hallucination detection large language model citation grounding",
    "LangGraph stateful multi-agent orchestration",
    "contract risk review legal large language model",
]


async def main():
    from src.agents.retriever.agent import RetrieverAgent
    agent = RetrieverAgent()
    total = 0
    for i, q in enumerate(SEED_QUERIES, 1):
        print(f"\n[{i}/{len(SEED_QUERIES)}] {q}")
        state = {
            "user_query": q,
            "research_topic": q,
            "session_id": f"build-{i}",
            "user_id": "eval-user",
            "kb_id": "default",
        }
        t0 = time.perf_counter()
        try:
            r = await agent.execute(state)
            n = (r.data or {}).get("count", 0)
            total += n
            print(f"  -> {n} papers, {time.perf_counter()-t0:.1f}s, conf={r.confidence:.2f}")
        except Exception as e:
            print(f"  -> FAILED: {e}")
    print(f"\nDone. Total papers indexed this run: {total}")


if __name__ == "__main__":
    asyncio.run(main())
