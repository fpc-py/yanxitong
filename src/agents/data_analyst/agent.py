"""Data Analyst Agent v2.0 — 数据分析智能体 with file upload support + Debug loop."""

import logging, os
from src.agents.base import BaseAgent, AgentResult
from src.tools.sandbox import get_sandbox

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Generate Python data analysis code for a CSV dataset.

Intent: {intent}

The data is at /workspace/data.csv. Read it with: df = pd.read_csv('/workspace/data.csv')

Requirements:
1. Use pandas, numpy, matplotlib, seaborn, scipy
2. Print column names, shape, dtype, and describe() first
3. Perform analysis matching the user intent
4. Save every chart to /output/ (e.g. plt.savefig('/output/chart1.png', dpi=110, bbox_inches='tight')).
   /output is the ONLY writable persistent directory; files written there are returned to the user.
   Do NOT save charts to /workspace (it is a throwaway tmpfs).
5. Print key findings in Chinese, including the concrete numbers you computed
6. Handle errors gracefully with try/except
7. NEVER create synthetic or sample data — the uploaded file already exists at the path above.
   Always load and analyse the real file.
8. Return ONLY Python code, no markdown fences, no explanations"""

DEBUG_PROMPT = """Fix the code error. Return ONLY the fixed complete Python code, no explanations.

Intent: {intent}
Error: {error}
Current code:
{code}"""

NO_DATA_PROMPT = """Generate a self-contained Python data analysis demo. Create sample data matching the intent, then analyze it.

Intent: {intent}

Create a sample dataset using numpy/pandas that illustrates the analysis. Then analyze it.
Save charts to /output/ (e.g. plt.savefig('/output/chart1.png', dpi=110, bbox_inches='tight')); /output is the only
persistent writable directory. Print findings in Chinese with concrete numbers.
Return ONLY Python code, no markdown fences."""

CODE_PREAMBLE = """# --- 环境预设（由平台注入）：无头后端 + 中文字体 ---
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
# ----------------------------------------------------
"""


class DataAnalystAgent(BaseAgent):
    name = "data_analyst"
    description = "数据分析智能体 v2.0 - Docker沙箱执行 + 文件上传支持 + 自动Debug闭环"
    model_role = "lightweight"

    async def _execute_impl(self, state: dict) -> AgentResult:
        intent = state.get("user_query", "数据分析")
        data_file = state.get("data_file_path", "")
        has_data = bool(data_file and os.path.exists(data_file))

        self._audit("analysis_start", {"intent": intent, "has_data_file": has_data})

        # Generate appropriate code
        if has_data:
            prompt = ANALYSIS_PROMPT.format(intent=intent)
        else:
            prompt = NO_DATA_PROMPT.format(intent=intent)

        # 关闭推理模型的隐藏思考：否则思考会耗尽 token 预算、返回空代码
        code = self._clean(await self._call_llm(prompt, enable_thinking=False))
        if not code:
            return AgentResult(success=False, error="模型未返回可执行代码，请重试。", confidence=0.0)
        self._audit("code_generated", {"length": len(code), "has_data": has_data})

        sandbox = get_sandbox()
        final = None

        for attempt in range(3):
            try:
                # 注入中文渲染预设，避免图表里的中文变成方框
                exec_code = CODE_PREAMBLE + "\n" + code
                if has_data:
                    result = await sandbox.execute_with_file(exec_code, data_file)
                else:
                    result = await sandbox.execute(exec_code)
            except Exception as e:
                result = {"stdout": "", "stderr": str(e), "exit_code": -1, "timed_out": False}

            if result["exit_code"] == 0 and not result["timed_out"]:
                final = result
                self._audit("execution_success", {"attempt": attempt + 1})
                break

            self._audit("execution_failed", {
                "attempt": attempt + 1,
                "error": result.get("stderr", "")[:200]
            })

            if attempt < 2:
                try:
                    code = await self._call_llm(
                        DEBUG_PROMPT.format(
                            intent=intent,
                            error=result.get("stderr", "")[:1000],
                            code=code,
                        ),
                        enable_thinking=False,
                    )
                    code = self._clean(code)
                except Exception:
                    pass
            else:
                final = result

        if final is None:
            return AgentResult(success=False, error="All code execution attempts failed", confidence=0.0)

        report = {
            "intent": intent,
            "stdout": final.get("stdout", "")[:5000],
            "stderr": final.get("stderr", "")[:1000],
            "exit_code": final["exit_code"],
            "timed_out": final.get("timed_out", False),
            "code": code,
            "has_data_file": has_data,
            # 沙箱从容器 /output 挂载目录取回的图片（data URL），供前端直接展示
            "figures": final.get("figures", []),
            "findings": [l.strip() for l in final.get("stdout", "").split("\n") if l.strip()][:20],
        }

        return AgentResult(
            success=final["exit_code"] == 0,
            data=report,
            confidence=0.9 if final["exit_code"] == 0 else 0.3,
        )

    @staticmethod
    def _clean(c: str) -> str:
        c = c.strip()
        if c.startswith("```python"):
            c = c[9:]
        elif c.startswith("```"):
            c = c[3:]
        if c.endswith("```"):
            c = c[:-3]
        return c.strip()