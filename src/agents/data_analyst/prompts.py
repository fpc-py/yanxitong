"""数据分析智能体 9 段流水线的全部提示词与代码注入块。

模板用 ``__TOKEN__`` 占位（``.replace`` 填充），避免 Python 代码片段里的
花括号与 ``str.format`` 冲突。

输出契约（生成代码与 prompts 共同约定）：
- 数据读取：``df = load_data()``（preamble 注入，格式自适应，禁止硬编码路径）；
- 展示图：``save_figure('name')`` → /output/name.png（150dpi，进前端展示通道）；
- 出版资产：同一 helper 顺带写 /output/pub/name_300dpi.{png,svg,pdf}；
- 结构化发现：``emit_findings({...})`` → stdout sentinel ``__YXT_FINDINGS__`` + JSON；
  统计列块形如 ``statistics[col] = {mean,std,min,max,median,n}``，供确定性校验。
"""

FINDINGS_SENTINEL = "__YXT_FINDINGS__"

CODE_PREAMBLE = r'''# --- 环境预设（平台注入，勿改动） -----------------------------------------
import json as _json
import os as _os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 150
plt.rcParams['savefig.dpi'] = 150
plt.rcParams['font.size'] = 8.5

# Okabe-Ito 无障碍调色板（色盲安全，禁止用默认 C0/C1 表达正负对比）
PALETTE = ['#000000', '#E69F00', '#56B4E9', '#009E73', '#F0E442', '#0072B2', '#D46A6A', '#CC79A7']
plt.rcParams['axes.prop_cycle'] = plt.cycler(color=PALETTE)

np.random.seed(42)

OUT_DIR = '/output'
PUB_DIR = '/output/pub'
_os.makedirs(PUB_DIR, exist_ok=True)

DATA_CONTAINER_PATH = __YXT_DATA_PATH__
DATA_ORIGINAL_NAME = __YXT_ORIGINAL_NAME__


def load_data(path=None):
    """读取上传数据（格式自适应；Mock/容器路径自动回退；禁止在生成代码里另写读取逻辑）。"""
    import pandas as pd
    p = path or _os.environ.get('YXT_MOCK_DATA_FILE') or DATA_CONTAINER_PATH
    if not p:
        raise FileNotFoundError('未找到上传数据文件')
    low = str(p).lower()
    if low.endswith(('.xlsx', '.xls', '.xlsm')):
        return pd.read_excel(p)
    if low.endswith(('.json', '.jsonl')):
        try:
            return pd.read_json(p)
        except ValueError:
            with open(p, encoding='utf-8') as fh:
                return pd.json_normalize(_json.load(fh))
    if low.endswith(('.parquet', '.pq')):
        return pd.read_parquet(p)
    for enc in ('utf-8', 'gbk', 'latin-1'):
        try:
            return pd.read_csv(p, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(p, encoding='utf-8', errors='replace')


def save_figure(name, dpi=150):
    """保存当前图形：展示用 150dpi PNG + 出版资产（300dpi PNG / SVG / PDF）。"""
    safe = str(name).strip().replace('/', '_').replace('\\', '_') or 'figure'
    for ext in ('.png', '.svg', '.pdf', '.jpg', '.jpeg'):
        if safe.lower().endswith(ext):
            safe = safe[: -len(ext)]
            break
    safe = safe or 'figure'
    fig = plt.gcf()
    fig.savefig(_os.path.join(OUT_DIR, safe + '.png'), dpi=dpi, bbox_inches='tight')
    for ext in ('png', 'svg', 'pdf'):
        fig.savefig(_os.path.join(PUB_DIR, safe + '_300dpi.' + ext), dpi=300, bbox_inches='tight')
    plt.close(fig)
    return safe + '.png'


def emit_findings(payload):
    """输出结构化发现（平台解析用，全文只调用一次；数值用 float()/int() 保证是原生类型）。"""
    print('__YXT_FINDINGS__' + _json.dumps(payload, ensure_ascii=True, default=str))
# --------------------------------------------------------------------------
'''


def build_preamble(data_container_path: str = "", original_name: str = "") -> str:
    import json as _json

    return (
        CODE_PREAMBLE
        .replace("__YXT_DATA_PATH__", _json.dumps(data_container_path or None))
        .replace("__YXT_ORIGINAL_NAME__", _json.dumps(original_name or "", ensure_ascii=True))
    )


PLANNING_PROMPT = """你是资深数据科学家的任务规划模块。先理解意图，再基于数据画像与已召回的方法/模板知识，规划一个可执行的分析任务计划。

用户意图：__INTENT__

数据画像：
__PROFILE_DIGEST__

已召回的知识（统计方法库/绘图模板库/期刊规范库/学科教材库）：
__RECALL_DIGEST__

只输出一个 JSON 对象（不要 markdown 代码块，不要多余文字），结构：
{
  "intent_summary": "一句话复述分析意图（中文）",
  "steps": [{"id": 1, "type": "clean|stats|model|viz|report", "description": "该步骤做什么（中文）", "depends_on": [], "output": "预期产出"}],
  "methods": [{"name": "统计方法名", "why": "为什么适用", "assumptions": ["前提假设"], "source": "统计方法库/小节名"}],
  "templates": [{"name": "绘图模板名", "when": "何时使用", "params": "关键参数", "source": "绘图模板库/小节名"}],
  "journal_rules": ["需要遵守的期刊制图规范（来自期刊规范库）"]
}

要求：
1. steps 3-6 步，覆盖数据清洗/校验、描述统计、统计推断或建模、可视化、报告；
2. methods/templates 必须来自上方已召回知识，禁止编造来源；召回为空则相应数组留空；
3. journal_rules 引自期刊规范库；召回没有则给 2 条通用规范；
4. 全部输出中文；值必须是字符串，不要嵌套多余结构。"""


CODEGEN_PROMPT = """你是数据分析代码生成助手。为下面的任务生成一段完整可运行的 Python 代码（单文件、单脚本）。

用户意图：__INTENT__

数据画像：
__PROFILE_DIGEST__

任务计划（按此执行）：
__PLAN_JSON__

知识召回（方法与绘图模板，请落实其中的做法）：
__RECALL_DIGEST__

执行环境（已由平台注入，代码中不要重复导入设置）：
- `df = load_data()` 读取上传数据：禁止硬编码文件路径、禁止用 pd.read_csv 另写读取逻辑、禁止生成模拟数据；
- 可用 `PALETTE`（Okabe-Ito 色盲安全八色列表）与 `save_figure('name')`、`emit_findings({...})`；
- 可用库：pandas、numpy、scipy、matplotlib、seaborn、scikit-learn；无网络。

硬性输出契约：
1. 第一件事 `df = load_data()`；随后打印数据形状与列名。
2. 每张图：用 matplotlib/seaborn 绘制，标题/轴标签用中文（含单位），颜色只用 PALETTE（或用 plt 默认色环已设为 PALETTE），字号不用小于 7pt；
   绘制完成后必须调用 `save_figure('中文图名')`（一次一张，不要用 plt.savefig 另存）。
3. 统计输出：均值/标准差/样本量等要 print 到 stdout（中文，含具体数字）；
   做统计检验请用 scipy：报告检验名、统计量、p 值、样本量；统计量用正确符号（t/U/F/χ²/r/ρ），不得混用；
   效应量须与检验方法自洽：非参数检验给 rank-biserial / Cliff's delta（或注明 Cohen's d 仅为描述性统计）。
4. 可视化要求：分组比较画误差线（±SE 或 ±SD 并注明）；有组间检验时在图上标注显著性（* p<0.05, ** p<0.01, *** p<0.001）；
   箱线图标注离群点；相关/回归图给出拟合线与 R²。
5. 如做了缺失值处理/异常值处理/类型转换等清洗：把清洗后的数据保存到 /output/cleaned.csv（`df.to_csv('/output/cleaned.csv', index=False)`）。
6. 结束前调用一次 `emit_findings(...)`，结构如下（数值用 float()/int() 包裹成原生类型）：
   {"summary": "含关键数字的一句话结论",
    "tests": [{"name": "检验名", "statistic": 0.0, "p_value": 0.0, "groups": ["组A","组B"], "assumptions": "前提检查结论"}],
    "statistics": {"列名": {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "median": 0.0, "n": 0}},
    "percentages": {"类别": 0.0},
    "charts": ["图名.png"],
    "notes": ["局限或注意事项"]}
   （percentages 只在确实计算了类别占比时给出且合计约 100；tests/statistics 没有就留空对象/数组）
   措辞纪律：summary 必须由代码根据实际结果动态拼装，禁止把结论词写成与数值无关的固定字符串；
   p 值展示统一样式 `"p<0.001" if p < 0.001 else f"p={p:.3f}"`（不要写 "p<0.435" 这类反向关系）；
   统计量标签必须与检验名联动（t 检验→t、Mann-Whitney U→U、ANOVA→F、卡方→χ²、Pearson→r、Spearman→ρ）；
   若前提检验被拒绝（如正态性 p<0.05）而因此换用非参数方法，summary/tests 要如实写明所用检验名与前提结论。
   必须照此模式拼装（示例，注意每个结论必须配自己的 p 值文本，不得混用）：
     ```python
     sig_corr = "存在显著" if p_pearson < 0.05 else "无显著"
     sig_diff = "存在显著差异" if p_compare < 0.05 else "无显著差异"
     p_corr_txt = "p<0.001" if p_pearson < 0.001 else f"p={p_pearson:.3f}"
     p_diff_txt = "p<0.001" if p_compare < 0.001 else f"p={p_compare:.3f}"
     # 标签/取值判断用完整词，严禁 `'t' in test_name`（"Whitney" 里也含字母 t 会导致取到 None！）
     is_mann = "Mann" in test_name
     stat_label = "U" if is_mann else "t"
     stat_value = u_stat if is_mann else t_stat           # 未执行分支的统计量是 None，绝不能格式化
     emit_findings({"summary": f"Score 与 Hours {sig_corr}线性相关 (r={r_pearson:.3f}, {p_corr_txt})；A组与B组{sig_diff}（{test_name}，{stat_label}={stat_value:.3f}，{p_diff_txt}）", ...})
     ```
   拼接任何 f-string 前，先确保其中每个变量都已赋值且非 None（未执行的检验分支的统计量必须先归一化）。
7. 不要写 try/except 吞掉异常；让错误真实抛出（有自动修复环节）。不要生成命令行参数解析、文件对话等无关代码。
8. 只输出 Python 代码本身：不要 markdown 代码块围栏，不要任何解释文字。"""


DEBUG_PROMPT = """修复下面代码的运行错误，返回完整修复后的代码。

用户意图：__INTENT__
错误信息：
__ERROR__

当前代码：
__CODE__

要求：
1. 只返回修复后的完整 Python 代码（不要围栏、不要解释）；
2. 保持原有分析逻辑与输出契约不变：数据用 `df = load_data()` 读取、图用 `save_figure('名')` 保存、
   结束前 `emit_findings(...)` 输出结构化发现；summary 中结论词（显著/不显著）必须按实际 p 值动态拼装、
   统计量标签与检验名一致（t/U/F/χ²/r/ρ）；
3. 常见原因优先自检：列名不存在（先 print(df.columns) 或用画像给的列名）、中文路径/编码、
   分组变量取值个数、空值导致统计失败、绘图元素过多、
   f-string 拼装报 TypeError/NoneType 格式化（未执行的检验分支变量先归一化赋值再拼接；
   注意 `'t' in 'Mann-Whitney U 检验'` 为真——"Whitney" 含字母 t——标签判断必须用 "Mann" 等完整词）。"""


VALIDATE_PROMPT = """你是分析结果审核员。核对下面的统计发现与叙述是否自洽、方法前提是否满足。

用户意图：__INTENT__

数据画像：
__PROFILE_DIGEST__

结构化发现（若为空说明脚本未按契约输出）：
__FINDINGS_JSON__

脚本输出摘要（stdout 前 1500 字）：
__STDOUT__

只输出一个 JSON 对象（不要围栏、不要多余文字）：
{
  "assumptions": [{"test": "检验名", "assumption": "正态性/方差齐性/独立性/样本量", "status": "ok|violated|unknown", "note": "判定依据"}],
  "narrative": [{"claim": "报告中的关键结论", "status": "consistent|inconsistent|unverifiable", "note": "与数字是否一致"}],
  "overall": "pass|warn|fail",
  "comments": ["具体问题与修改建议（中文）"]
}

判定口径：p 值/统计量/均值与样本量明显矛盾、结论超出数据支持范围、检验前提严重不满足时给 fail；
样本偏小、前提未检查等给 warn；一切自洽给 pass。"""


INTERPRET_PROMPT = """你是资深数据分析师，为下面的分析结果撰写中文解读报告（Markdown）。

用户意图：__INTENT__

数据画像：
__PROFILE_DIGEST__

任务计划（steps 摘要）：
__PLAN_STEPS__

结构化发现：
__FINDINGS_JSON__

脚本输出（stdout 前 2500 字）：
__STDOUT__

确定性校验结论：
__VALIDATION_SUMMARY__

写作要求：
1. 只允许引用上面出现过的数字，禁止编造任何数值或文献；发现里的数字与 stdout 冲突时以明确的那一个为准；
2. 用 `## ` 分节：背景与问题、数据与方法、主要发现（每条带具体数字）、统计检验（检验名/p/效应量）、假设与局限、图表说明、下一步建议；
3. 引用统计方法时标注来源库署名，如「（统计方法库：独立样本 t 检验）」；引用了绘图模板同理；
4. 如果校验结论里有 fail/warn 项，在「假设与局限」中原样说明；
5. 语言客观精炼，总长 400-800 字；只输出 Markdown 正文，不要用代码块包裹全文。"""


DEGRADED_SCRIPT = r'''# --- 静态降级脚本（无 LLM；执行失败 3 次后兜底） ---
import numpy as np
import pandas as pd

df = load_data()
print('== 降级模式：基础统计 ==')
print('数据形状:', df.shape)
print('列名:', list(df.columns)[:40])
print(df.describe(include='all').to_string()[:1800])

numeric = df.select_dtypes(include=[np.number])
missing = {str(c): int(df[c].isna().sum()) for c in df.columns}
charts = []
for col in list(numeric.columns)[:2]:
    series = numeric[col].dropna()
    if len(series) < 2:
        continue
    fig, ax = plt.subplots(figsize=(3.6, 2.6))
    ax.hist(series, bins=min(20, max(5, len(series) // 5)), color=PALETTE[5], edgecolor='white')
    ax.set_title(str(col) + ' 分布', fontsize=9)
    ax.set_xlabel(str(col))
    ax.set_ylabel('频数')
    charts.append(save_figure('降级_' + str(col) + '_分布'))
    print('列 %s: 均值=%.4f 标准差=%.4f 样本量=%d' % (col, series.mean(), series.std(ddof=1) if len(series) > 1 else 0.0, len(series)))

stats = {}
for col in list(numeric.columns)[:20]:
    s = numeric[col].dropna()
    if not len(s):
        continue
    stats[str(col)] = {
        'mean': float(s.mean()), 'std': float(s.std(ddof=1)) if len(s) > 1 else 0.0,
        'min': float(s.min()), 'max': float(s.max()), 'median': float(s.median()), 'n': int(len(s)),
    }
emit_findings({
    'summary': '降级模式：完成基础统计与分布图（%d 行 × %d 列）' % df.shape,
    'tests': [], 'statistics': stats, 'charts': charts,
    'notes': ['自动修复 3 次仍失败，已降级为基础统计脚本；结论有限，请检查数据或换一种提问方式'],
})
print('降级分析完成，图表数:', len(charts))
'''


# ---- 摘要构造（供 agent 注入 prompt） -----------------------------------------

def profile_digest(profile: dict | None, max_cols: int = 30) -> str:
    if not isinstance(profile, dict) or not profile:
        return "（无数据画像：本次未上传数据文件）"
    if profile.get("degraded"):
        return f"（画像降级不可用：{profile.get('error', '未知原因')}）"
    lines = [
        f"格式: {profile.get('format', '?')} | 行数: {profile.get('rows', '?')} | 列数: {profile.get('cols', '?')}"
    ]
    for col in (profile.get("columns") or [])[:max_cols]:
        extra = ""
        if col.get("min") is not None and col.get("max") is not None:
            extra = f" 范围[{col['min']}, {col['max']}]"
        lines.append(
            f"- {col.get('name')} ({col.get('dtype')}) 缺失{col.get('missing_pct', 0)}%"
            f" 唯一值{col.get('unique', '?')}{extra} 示例: {col.get('sample', '')}"
        )
    if profile.get("columns_truncated"):
        lines.append("（列过多，仅展示前 30 列）")
    issues = (profile.get("quality") or {}).get("issues") or []
    for issue in issues[:10]:
        lines.append(f"  质量提示[{issue.get('kind')}] {issue.get('column', '')}: {issue.get('detail', '')}")
    return "\n".join(lines)


def recall_digest(recall: dict | None, max_chunks_per_lib: int = 3, max_chars: int = 420) -> str:
    if not isinstance(recall, dict) or not recall.get("libraries"):
        return "（无召回知识）"
    lines: list[str] = []
    for lib, info in recall["libraries"].items():
        chunks = (info or {}).get("chunks") or []
        if not chunks:
            continue
        lines.append(f"【{info.get('label', lib)}】")
        for chunk in chunks[:max_chunks_per_lib]:
            text = (chunk.get("text") or "").strip().replace("\n", " ")
            lines.append(f"- [来源: {chunk.get('source', lib)} | {chunk.get('section', '')}] {text[:max_chars]}")
    if recall.get("degraded"):
        lines.append("（知识库向量检索降级为关键词召回，来源标注可能不完整）")
    return "\n".join(lines) if lines else "（无召回知识）"


def plan_steps_digest(plan: dict | None) -> str:
    steps = (plan or {}).get("steps") or []
    if not steps:
        return "（无显式步骤）"
    return "\n".join(
        f"{s.get('id', i + 1)}. [{s.get('type', '?')}] {s.get('description', '')} → {s.get('output', '')}"
        for i, s in enumerate(steps)
    )


def heuristic_plan(intent: str) -> dict:
    """LLM 规划失败时的启发式单步计划（按关键词猜测类型与通用方法）。"""
    lowered = intent or ""
    method_hits: list[dict] = []
    if any(k in lowered for k in ("检验", "显著", "差异", "比较", "对比")):
        method_hits.append({
            "name": "组间差异检验（t 检验 / Mann-Whitney U）",
            "why": "意图包含组间比较，先做正态性与方差齐性检查再选择参数/非参数检验",
            "assumptions": ["分组独立", "近正态或大样本", "方差齐性（Welch 校正可放宽）"],
            "source": "统计方法库（启发式兜底）",
        })
    if any(k in lowered for k in ("相关", "关系", "影响", "回归")):
        method_hits.append({
            "name": "相关分析与线性回归",
            "why": "意图涉及变量关系，计算 Pearson/Spearman 相关并给出拟合与 R²",
            "assumptions": ["线性关系（回归）", "无严重多重共线性"],
            "source": "统计方法库（启发式兜底）",
        })
    return {
        "intent_summary": intent,
        "steps": [{
            "id": 1,
            "type": "stats",
            "description": f"围绕意图执行完整分析：{intent}",
            "depends_on": [],
            "output": "统计结论 + 图表 + 结构化发现",
        }],
        "methods": method_hits,
        "templates": [],
        "journal_rules": ["字号不小于 7pt；使用色盲安全配色；图注含必要统计信息"],
        "fallback": True,
    }
