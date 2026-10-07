"""实验设计引擎的提示词模板（规格①解析 / ②诊断 / 候选 / 优化 / 校验 / 代码）。

所有提示词遵循同一数据契约：模型只能引用「证据锚点」列表中出现过的锚点 id
（``kg:ent:*`` / ``kg:chain:*`` / ``kb:*`` / ``paper:*``），引用不到的结论必须
剔除——对应规格⑪"无引用不建议"。
"""

from __future__ import annotations

import json

PARSE_PROMPT = """你是一个实验配置解析器。从用户请求中提取"当前实验配置"，缺失字段留空，
不要编造。

用户请求:
{query}

用户显式提供的配置(可能为空):
{provided}

返回JSON (no markdown fences):
{{
    "task": "任务描述（分类/检测/分割/...）",
    "model": {{"name": "当前模型名", "family": "cnn|vit|transformer|lstm|gnn|other", "params_m": 0}},
    "hyperparams": {{
        "learning_rate": null, "batch_size": null, "optimizer": "", "scheduler": "",
        "epochs": null, "weight_decay": null, "dropout": null
    }},
    "data": {{"name": "数据集名", "n_samples": null, "n_classes": null, "augmentation": []}},
    "resources": {{"gpu": "", "gpu_hours": null, "memory_gb": null}},
    "metrics": ["accuracy"],
    "missing": ["用户未提供、但做方案所必需的字段名"]
}}
数值无法确定时用 null，文本无法确定时用空串。所有文本字段使用中文。"""


DIAGNOSE_PROMPT = """你是一个实验方案审计专家。对比"当前配置"与"证据锚点"，找出瓶颈。

当前配置:
{config}

证据锚点（每条以 [锚点id] 开头；只能引用这里出现过的 id）:
{evidence}

可选的历史实验结果先验（来自本课题组既往运行，可能为空）:
{priors}

请逐类检查五类瓶颈（没有的类别不要输出）：
1. model_outdated：模型落后于同任务在证据中出现的方法/年份；
2. hyperparam_drift：学习率/批大小/优化器等偏离证据中的先验区间；
3. data_insufficient：样本量/类别数不足以支撑目标结论（参考统计功效要求）；
4. strategy_missing：缺少证据中普遍采用的增强/正则/调度策略；
5. resource_mismatch：GPU 时长/显存估算与给定资源不匹配。

返回JSON (no markdown fences):
{{
    "bottlenecks": [
        {{"type": "model_outdated|hyperparam_drift|data_insufficient|strategy_missing|resource_mismatch",
          "severity": "high|medium|low",
          "finding": "一句话瓶颈描述",
          "evidence_refs": ["kg:ent:xxx", "kb:design:0"],
          "recommendation": "改进方向"}}
    ],
    "summary": "总体诊断结论"
}}

硬性要求：每条瓶颈的 evidence_refs 至少包含一个真实存在的锚点 id；找不到证据支撑的瓶颈
一律不要输出。所有文本字段使用中文。"""


CANDIDATE_PROMPT = """你是一个实验方案生成器。基于瓶颈诊断与证据锚点，为用户当前实验生成
4-8 个差异化候选方案（至少覆盖 3 种路由）。

用户意图: {intent}

当前配置:
{config}

瓶颈诊断:
{diagnosis}

证据锚点（每条以 [锚点id] 开头；只能引用这里出现过的 id）:
{evidence}

历史先验（可能为空）:
{priors}

路由定义：
- structure：更换/升级模型结构或骨干网络；
- hyperparam：调整学习率/批大小/优化器/调度器/正则等超参；
- data：数据增强、采样、划分、扩充策略；
- strategy：训练策略（迁移学习、课程学习、蒸馏、集成、早停等）。

返回JSON (no markdown fences):
{{
    "candidates": [
        {{"route": "structure|hyperparam|data|strategy",
          "title": "方案短名",
          "description": "一句话方案描述",
          "config_patch": {{"model.name": "新模型", "hyperparams.learning_rate": 0.001}},
          "hyperparams": {{"learning_rate": 0.001, "batch_size": 64, "optimizer": "adamw",
                           "scheduler": "cosine", "epochs": 100, "weight_decay": 0.05, "dropout": 0.1}},
          "evidence_refs": ["kg:chain:xxx", "kb:design:1"],
          "expected": "定性预期收益（不得编造具体数字）",
          "risk": "low|medium|high",
          "complexity": "low|medium|high",
          "interpretability": "high|medium|low"}}
    ]
}}

硬性要求：每个候选的 evidence_refs 至少包含一个真实存在的锚点 id；引用不到证据的候选
不要输出。超参必须落在证据给出的先验区间内（若证据中有区间）。所有文本字段使用中文。"""


VALID_ROUTES = ("structure", "hyperparam", "data", "strategy")

VALIDATE_PROMPT = """你是一个实验方案校验者。对推荐方案做一致性复核，只报告有依据的问题。

推荐方案:
{recommended}

证据锚点（每条以 [锚点id] 开头）:
{evidence}

确定性检查结果:
{deterministic}

验证计划:
{plan}

请检查：①方案主张与证据是否一致（不得支持与证据矛盾的结论）；②估计值是否被误当作
真实结果陈述；③不确定度措辞是否充分；④验证计划是否覆盖该方案的关键改动。

返回JSON (no markdown fences):
{{
    "consistency": "pass|warn|fail",
    "issues": [
        {{"claim": "问题主张", "problem": "问题描述",
          "severity": "high|medium|low", "evidence_ref": "锚点id或空串"}}
    ],
    "narrative": "复核结论（中文，说明未发现问题的方面也有依据）"
}}

硬性：不编造输入中不存在的数值；没有证据支撑的指控不要输出。所有文本字段使用中文。"""


CODEGEN_PROMPT = """你是一个机器学习工程专家。根据推荐实验配置生成一个可运行的 PyTorch 训练脚本。

用户意图: {intent}

推荐配置（含证据与估计元数据）:
{config}

证据锚点（实现可参考；每条以 [锚点id] 开头）:
{evidence}

要求：
- 只输出 Python 代码，不要 markdown 代码围栏、不要解释文字；
- 顶部固定随机种子（python/numpy/torch 均用 SEED=42）；
- 配置常量区包含推荐的学习率/批大小/优化器/调度器/轮数/权重衰减/dropout 与数据增强；
- 结构：数据加载（含训练/验证划分与增强）、模型构建、训练循环（含早停）、验证与指标记录；
- 训练循环内的每行代码必须真实可运行，不得写 TODO/伪代码；
- 首行注释写明：本脚本在具备相应 GPU 与依赖的训练环境执行（沙箱仅做静态校验）。

只输出代码。"""


REPORT_PROMPT = """你是科研实验方案报告撰写者。基于结构化结果撰写中文 markdown 报告。

用户意图: {intent}

结构化结果（只能引用其中出现过的数值；估计值必须标注"估计"）:
{payload}

格式要求（小节标题固定，缺数据的小节写"暂无"并说明原因）:
# 实验方案报告
## 研究假设
## 变量设计
## 统计方法
## 瓶颈诊断
## 证据与候选方案
## 多目标优化与推荐配置
## 可行性与验证计划
## 风险与回退
## 数据（估计值）说明

硬性：不得编造输入中不存在的数字或文献；所有内容使用中文。只输出 markdown。"""

_CATEGORICAL_FIELDS = {
    "risk": {"low": 0.2, "medium": 0.5, "high": 0.8},
    "complexity": {"low": 0.2, "medium": 0.5, "high": 0.8},
    "interpretability": {"high": 0.8, "medium": 0.5, "low": 0.2},
}


def format_config(config: dict | None) -> str:
    if not config:
        return "（用户未提供当前配置）"
    return json.dumps(config, ensure_ascii=False, indent=1)[:2000]


def format_evidence(evidence: dict, limit: int = 40) -> str:
    """把 EvidenceSet.anchors 渲染成提示词友好的锚点清单。"""
    anchors = (evidence or {}).get("anchors") or []
    if not anchors:
        return "（无可用证据锚点）"
    lines = []
    for anchor in anchors[:limit]:
        text = str(anchor.get("text") or "")[:220]
        title = anchor.get("title") or anchor.get("name") or ""
        head = f"[{anchor.get('id')}] {anchor.get('kind')}"
        if title:
            head += f" · {str(title)[:80]}"
        lines.append(f"{head}\n  {text}")
    return "\n".join(lines)


def format_priors(priors: dict | None) -> str:
    """把 prior_store.query_priors 的结果渲染成紧凑文本。"""
    if not priors or not priors.get("rows"):
        return "（无历史先验）"
    lines = []
    for name, stat in (priors.get("hyperparams") or {}).items():
        if stat.get("n"):
            lines.append(
                f"{name}: 区间 [{stat.get('min')}, {stat.get('max')}] "
                f"均值 {stat.get('mean')}（n={stat.get('n')}）"
            )
    for row in (priors.get("metrics") or [])[:8]:
        lines.append(
            f"{row.get('method')}/{row.get('dataset')} 的 {row.get('metric')}: "
            f"均值 {row.get('mean')} ± {row.get('std')} "
            f"区间 [{row.get('min')}, {row.get('max')}]（n={row.get('n')}）"
        )
    cost = priors.get("cost") or {}
    if cost.get("n"):
        lines.append(f"成本: 均值 {cost.get('mean')}，区间 [{cost.get('min')}, {cost.get('max')}]（n={cost.get('n')}）")
    duration = priors.get("duration") or {}
    if duration.get("n"):
        lines.append(f"时长: 均值 {duration.get('mean')} 小时，区间 [{duration.get('min')}, {duration.get('max')}]（n={duration.get('n')}）")
    return "\n".join(lines) or "（无历史先验）"
