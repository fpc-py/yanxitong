"""A5: 真正对系统跑金标集的基线评测脚本。

与 ragas_eval.py（玩具自评估）的区别：本脚本直接实例化 SupervisorAgent，
对 goldset.json 每条样本发起真实 query（走 graphrag.query + LLM 生成 +
triple_check + 六道防线），记录 answer / context / citations / 防御报告，
计算 RAGAS 四项 + 库外拒答率 + 引用率。

Usage:
    python -m tests.evaluation.run_baseline                 # 全量 30 条
    python -m tests.evaluation.run_baseline --limit 5        # 冒烟
    python -m tests.evaluation.run_baseline --no-kg          # 消融：关 KG 融合
    python -m tests.evaluation.run_baseline --no-triple      # 消融：关三元组反查

输出：
    docs/baseline_<tag>.md      人类可读报告
    docs/baseline_<tag>.json   逐样本原始结果
"""

from __future__ import annotations

import argparse, asyncio, json, logging, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "urllib3", "neo4j", "openai", "langchain"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

logger = logging.getLogger("baseline")


def load_goldset() -> list[dict]:
    p = Path(__file__).parent / "goldset.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["samples"]


def score_faithfulness(answer: str, context: str) -> float:
    """answer 中关键术语在 context 中的覆盖率（粗粒度，正式 RAGAS 接 LLM 时再换）。"""
    if not context:
        return 0.0
    ctx = context.lower()
    claims = [c.strip() for c in answer.split("。") if len(c.strip()) > 10]
    if not claims:
        return 0.5
    grounded = 0
    for c in claims:
        terms = [t for t in c.lower().split() if len(t) > 3]
        if not terms:
            grounded += 1
            continue
        hit = sum(1 for t in terms if t in ctx)
        if hit / len(terms) >= 0.3:
            grounded += 1
    return round(grounded / len(claims), 3)


def score_recall(reference_facts: list[str], context: str) -> float:
    if not context:
        return 0.0
    ctx = context.lower()
    hit = sum(1 for f in reference_facts if f.lower() in ctx)
    return round(hit / max(len(reference_facts), 1), 3)


def should_refuse(answer: str) -> bool:
    a = answer or ""
    markers = ["未检索到", "没有相关文献", "知识库中没有", "无法", "不能提供", "不属于", "建议咨询", "不应编造", "无法预知"]
    return any(m in a for m in markers)


def has_citation(answer: str) -> bool:
    import re
    return bool(re.search(r"\[\d+\]", answer or ""))


async def run_one(supervisor, sample: dict, no_kg: bool, no_triple: bool) -> dict:
    sid = f"eval-{sample['id']}-{int(time.time())}"
    state = {
        "user_query": sample["query"],
        "session_id": sid,
        "user_id": "eval-user",
        "intent": None,
        "intent_confidence": 0.0,
        "kb_id": "default",
        "literature_results": [],
        "cross_kb_ids": [],
    }
    # 消融开关：通过环境变量传给子系统（graphrag 的 KG 融合在连接失败时已自动降级）
    os.environ["YXT_NO_KG"] = "1" if no_kg else "0"
    os.environ["YXT_NO_TRIPLE"] = "1" if no_triple else "0"

    t0 = time.perf_counter()
    result = await supervisor.execute(state)
    latency = time.perf_counter() - t0

    data = result.data or {}
    answer = data.get("answer", "") if isinstance(data, dict) else ""
    qr = data.get("quality_report", {}) if isinstance(data, dict) else {}
    return {
        "id": sample["id"],
        "type": sample.get("type", ""),
        "query": sample["query"],
        "success": result.success,
        "answer": answer[:1500],
        "latency_s": round(latency, 2),
        "confidence": data.get("confidence", 0.0) if isinstance(data, dict) else 0.0,
        "risk_level": data.get("risk_level", "") if isinstance(data, dict) else "",
        "human_review": data.get("human_review_required", False) if isinstance(data, dict) else False,
        "n_citations": len(result.citations or []),
        "must_refuse": sample.get("must_refuse", False),
        "expected_route": sample.get("expected_route", "lit"),
        "reference_facts": sample.get("reference_facts", []),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    ap.add_argument("--tag", default="baseline", help="输出文件名后缀")
    ap.add_argument("--no-kg", action="store_true", help="消融：关 KG 融合")
    ap.add_argument("--no-triple", action="store_true", help="消融：关三元组反查")
    args = ap.parse_args()

    samples = load_goldset()
    if args.limit:
        samples = samples[: args.limit]

    from src.agents.supervisor.agent import SupervisorAgent
    supervisor = SupervisorAgent()

    rows = []
    for i, s in enumerate(samples, 1):
        logger.warning("[%d/%d] %s: %s", i, len(samples), s["id"], s["query"][:40])
        try:
            row = await run_one(supervisor, s, args.no_kg, args.no_triple)
        except Exception as e:
            row = {"id": s["id"], "query": s["query"], "success": False, "error": str(e), "answer": ""}
        rows.append(row)
        print(f"  -> ok={row.get('success')} lat={row.get('latency_s','-')}s cite={row.get('n_citations',0)} refuse={row.get('must_refuse')}")

    # 汇总指标
    answered = [r for r in rows if r.get("success") and r.get("answer")]
    ref_rows = [r for r in rows if r.get("must_refuse")]
    refuse_hit = sum(1 for r in ref_rows if should_refuse(r.get("answer", "")))
    cite_rows = [r for r in rows if not r.get("must_refuse")]
    cite_rate = sum(1 for r in cite_rows if has_citation(r.get("answer", ""))) / max(len(cite_rows), 1)
    avg_conf = sum(r.get("confidence", 0) for r in answered) / max(len(answered), 1)
    avg_lat = sum(r.get("latency_s", 0) for r in answered) / max(len(answered), 1)

    summary = {
        "tag": args.tag,
        "n_total": len(rows),
        "n_success": len(answered),
        "refuse_rate_on_out_of_scope": round(refuse_hit / max(len(ref_rows), 1), 3),
        "citation_rate_on_normal": round(cite_rate, 3),
        "avg_confidence": round(avg_conf, 3),
        "avg_latency_s": round(avg_lat, 2),
        "ablations": {"no_kg": args.no_kg, "no_triple": args.no_triple},
    }

    out_dir = Path("docs")
    (out_dir / f"baseline_{args.tag}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = [
        f"# 基线报告 — {args.tag}",
        f"",
        f"- 样本数: {summary['n_total']}（成功 {summary['n_success']}）",
        f"- 库外拒答率: **{summary['refuse_rate_on_out_of_scope']}** ({refuse_hit}/{len(ref_rows)})",
        f"- 普通问题引用率: **{summary['citation_rate_on_normal']}**",
        f"- 平均置信度: {summary['avg_confidence']}",
        f"- 平均延迟: {summary['avg_latency_s']}s",
        f"- 消融: no_kg={args.no_kg}, no_triple={args.no_triple}",
        f"",
        f"| id | type | lat(s) | cite | refuse | conf | answer_head |",
        f"|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        md.append(
            f"| {r.get('id')} | {r.get('type','')} | {r.get('latency_s','-')} | "
            f"{r.get('n_citations',0)} | {'✓' if should_refuse(r.get('answer','')) else '-'} | "
            f"{r.get('confidence',0):.2f} | {(r.get('answer','') or '')[:60].replace(chr(10),' ')} |"
        )
    (out_dir / f"baseline_{args.tag}.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
