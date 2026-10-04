"""Demo scenarios for 研析通 v2.0 — 3 end-to-end demonstration flows.

Scenario 1: Literature Survey (找→读)
Scenario 2: Data Analysis (算)
Scenario 3: Paper Review (写→审)

Usage:
    python -m tests.evaluation.demo_scenarios   # 打印格式化演示脚本
    python tests/evaluation/demo_scenarios.py   # 直接运行也可以（自动补 sys.path）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class DemoScenarios:
    """Pre-scripted demo scenarios for competition presentation."""

    SCENARIO_1 = {
        "name": "文献调研全链路",
        "description": "从提问到文献综述的完整流程：找→读→构建知识图谱",
        "steps": [
            {
                "step": 1,
                "action": "POST /api/session",
                "input": {"query": "Transformer模型在自然语言处理中的最新进展", "topic": "Transformer NLP"},
                "expected": "返回会话ID + 文献列表",
            },
            {
                "step": 2,
                "action": "GET /api/session/{id}",
                "input": {},
                "expected": "显示文献数量、KG实体数、置信度评分",
            },
            {
                "step": 3,
                "action": "POST /api/session/{id}/query",
                "input": {"query": "BERT和GPT的核心区别是什么？"},
                "expected": "带引用标注的回答，显示[1][2]等引用",
            },
            {
                "step": 4,
                "action": "GET /api/session/{id}/citation-chain",
                "input": {},
                "expected": "完整引用溯源链：声明→原文摘录→来源",
            },
        ],
        "talking_points": [
            "演示GraphRAG双引擎：向量检索+知识图谱联合查询",
            "每个回答都带引用溯源，点击可查看原文摘录",
            "知识图谱自动从文献中抽取实体和关系",
        ],
    }

    SCENARIO_2 = {
        "name": "数据分析闭环",
        "description": "上传实验数据→自动分析→生成图表→实验设计",
        "steps": [
            {
                "step": 1,
                "action": "POST /api/session",
                "input": {"query": "分析深度学习模型性能数据", "topic": "DL Benchmark"},
                "expected": "创建会话",
            },
            {
                "step": 2,
                "action": "POST /api/session/{id}/upload",
                "input": {"file": "benchmark_results.csv"},
                "expected": "文件上传成功",
            },
            {
                "step": 3,
                "action": "POST /api/session/{id}/analyze",
                "input": {"query": "比较各模型在准确率和推理速度上的表现"},
                "expected": "统计分析结果 + 图表路径",
            },
            {
                "step": 4,
                "action": "POST /api/session/{id}/design",
                "input": {"query": "设计验证模型泛化能力的实验"},
                "expected": "实验方案（含假设、变量、统计方法）",
            },
        ],
        "talking_points": [
            "代码在Docker沙箱中安全执行，网络隔离+资源限制",
            "自动Debug闭环：代码报错→LLM分析→修复→重执行（最多3次）",
            "从数据分析结果自动生成实验设计方案",
        ],
    }

    SCENARIO_3 = {
        "name": "论文写作与审稿",
        "description": "基于文献自动生成论文→多维度审稿→修改建议",
        "steps": [
            {
                "step": 1,
                "action": "POST /api/session",
                "input": {"query": "Transformer注意力机制研究进展", "topic": "Attention Mechanism"},
                "expected": "文献检索完成",
            },
            {
                "step": 2,
                "action": "POST /api/session/{id}/write",
                "input": {"query": "全文 full_paper"},
                "expected": "生成完整论文章节",
            },
            {
                "step": 3,
                "action": "POST /api/session/{id}/review",
                "input": {"draft": "[GBT] <生成的论文>"},
                "expected": "IMRaD审稿报告 + 修改清单",
            },
            {
                "step": 4,
                "action": "POST /api/session/{id}/bibliography",
                "input": {"style": "gbt7714"},
                "expected": "GB/T 7714格式的参考文献列表",
            },
        ],
        "talking_points": [
            "基于文献自动生成论文初稿，含引用标注",
            "IMRaD七维度审稿：结构/逻辑/引用/数据/格式/语言/创新性",
            "支持APA/MLA/GB/T 7714三种引用格式",
            "六道幻觉防线确保生成内容可追溯",
        ],
    }

    SCENARIOS = ("scenario_1", "scenario_2", "scenario_3")

    @classmethod
    def generate_demo_script(cls) -> str:
        """Generate a formatted demo script for the presentation."""
        lines = [
            "=" * 70,
            "  研析通 v2.0 — 竞赛演示脚本",
            "=" * 70,
            "",
        ]

        for i, key in enumerate(cls.SCENARIOS, 1):
            scenario = getattr(cls, key.upper())
            lines.extend([
                f"## 演示场景 {i}: {scenario['name']}",
                f"  流程: {scenario['description']}",
                "",
                "  API调用序列:",
            ])
            for step in scenario["steps"]:
                lines.append(f"    {step['step']}. {step['action']}")
                lines.append(f"       输入: {json.dumps(step['input'], ensure_ascii=False)}")
                lines.append(f"       预期: {step['expected']}")
                lines.append("")

            lines.append("  🎤 讲解要点:")
            for tp in scenario["talking_points"]:
                lines.append(f"    • {tp}")
            lines.extend(["", "-" * 70, ""])

        lines.extend([
            "## 演示检查清单",
            "",
            "  □ Docker服务全部启动（neo4j, redis, jaeger, prometheus, grafana）",
            "  □ DASHSCOPE_API_KEY 已配置",
            "  □ Grafana仪表板展示实时指标",
            "  □ Jaeger显示全链路追踪",
            "  □ 三个演示场景均可独立运行",
            "  □ 消融实验报告已生成",
            "  □ README和API文档已完善",
            "",
            "=" * 70,
        ])

        return "\n".join(lines)

    @classmethod
    def export_json(cls, path: str | None = None) -> str:
        """Dump all scenarios to JSON (for building slides / checklist tools)."""
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_scenarios.json")
        payload = {name.upper(): getattr(cls, name.upper()) for name in cls.SCENARIOS}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path


if __name__ == "__main__":
    try:  # Windows console UTF-8 safety
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(DemoScenarios.generate_demo_script())
    if "--json" in sys.argv:
        out = DemoScenarios.export_json()
        print(f"\n[OK] 场景已导出: {out}")
