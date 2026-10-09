# 研析通 v3.0 — 开发日志

> 每次完成一个可独立交付的开发任务，在此追加一条记录。
> 只本地 commit，不推远程。计划见 [`NEXT_PLAN.md`](./NEXT_PLAN.md)。

---

## 2026-10-09

### A1 · 沙箱静默降级安全洞修复
- **问题**：`get_sandbox()` 在 docker daemon 不可用时静默回退 `MockSandbox`，在宿主直接执行 LLM 生成的任意 Python 代码（RCE 风险）。
- **改动**：
  - `src/core/exceptions.py` 新增 `SandboxUnavailableError`
  - `src/tools/sandbox.py`：docker 不可用时仅当 pytest 运行中或显式 `YXT_ALLOW_MOCK_SANDBOX=1` 才允许 MockSandbox，否则抛异常
  - `src/tools/profiler.py`、`src/agents/experiment_designer/codegen.py`、`src/api/routes.py` `/system/capabilities` 三处调用点全部包进降级路径
- **验证**：普通 python 进程正确抛 `SandboxUnavailableError`；opt-in 后才返回 MockSandbox；相关 46 单测通过。

### A3 · 文档一致性对齐
- **改动**：
  - 版本号统一 v3.0：`pyproject.toml`、`config.yaml`、`src/main.py`、`src/__init__.py`、supervisor description、对应测试断言
  - `README.md` 重写：删掉与代码不符的"任务分解/资源路由/冲突仲裁"描述，换成真实架构图、沙箱安全说明、4 项真壁垒清单
  - `docs/研析通v2.0_完整方案.md` 顶部加状态声明，列明 Celery/ES/Zotero/ELK/K8s 是目标态不是现状
  - `docs/PLAN.md` 末尾 16 条散办待办收敛指向 `NEXT_PLAN.md`

### A2 · 砍假结构——意图路由真接 LLM
- **问题**：`route_intent` 是关键词表匹配；SupervisorAgent 内部调 LLM 做 intent 但结果只写审计日志没人用；`task_queue/completed_tasks` 在 state 里定义但全 src 无人读写。
- **改动**：
  - `src/workflows/state.py`：删 `task_queue/completed_tasks` 死字段，新增 `intent`/`intent_confidence` 字段
  - `src/workflows/supervisor_graph.py`：新增 `_llm_classify_intent()`（lightweight 模型 + 关思考 + JSON 输出）；`route_intent` 改 async——显式 phase 直达，否则 LLM 分类置信度≥0.6 采用，否则回退关键词表
  - `src/agents/supervisor/agent.py`：删掉重复的 INTENT_PROMPT LLM 调用，直接读 state 里路由阶段已分类好的 intent
- **验证**：全量单测 402 passed, 1 skipped。

### A4 · RAGAS 金标集
- **改动**：
  - 新增 `tests/evaluation/goldset.json`：30 条样本，覆盖中文/英文/数字类/库外拒答/路由正确性/材料学科六类场景
  - `tests/evaluation/ragas_eval.py` 改为从 goldset.json 加载，替换硬编码 10 条
- **验证**：脚本能跑，30 条样本正确加载。

### A6 · 安全基线
- **改动**：
  - `src/main.py`：CORS 从 `allow_origins=["*"]` 收敛到 `YXT_CORS_ORIGINS` 环境变量（默认仅 localhost:4321），允许方法/头白名单
  - `docker-compose.yml`：Neo4j/MySQL/Grafana 密码全部改为 `${VAR:-default}` env 注入
  - 新增 `.env.example`
- **验证**：默认 CORS 仅前端 dev 域名，env 可覆盖。

---

### 待办（下一步）
- A5：起 docker 后跑 RAGAS 基线 + 消融实验（需 Neo4j + 真实 LLM）
- B1：接 bge-reranker-v2-m3 精排
