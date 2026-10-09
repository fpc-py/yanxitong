# 研析通  — 全阶段开发计划

## 一、项目总览

构建一个 **分层监督式多智能体科研系统**，覆盖"文献调研→知识构建→数据挖掘→实验设计→论文合规"五大环节。

***核心栈**：Python 3.12 + LangGraph + LangChain + FastAPI + Neo4j + FAISS/BGE-M3 + Docker + Redis  
**模型路由**：qwen-max (Supervisor主推理) / qwen3.8-flash (轻量任务) / deepseek-v4-pro (深度推理)

---

## 二、项目目录结构

```
yanxitong/
├── src/
│   ├── __init__.py
│   ├── core/                        # 核心基础设施
│   │   ├── config.py                # 全局配置 (YAML + env)
│   │   ├── llm_factory.py           # 多模型路由工厂
│   │   ├── cache.py                 # Redis 语义缓存 + Prompt压缩
│   │   └── exceptions.py            # 统一异常体系
│   ├── agents/                      # 五类 Agent 实现
│   │   ├── base.py                  # Agent 基类 (熔断器、审计日志)
│   │   ├── supervisor/              # Supervisor Agent
│   │   ├── retriever/               # 文献检索与解析
│   │   ├── kg_builder/              # 知识图谱构建
│   │   ├── data_analyst/            # 数据分析 (含Docker沙箱)
│   │   └── academic_reviewer/       # 学术审稿
│   ├── knowledge/                   # GraphRAG 双引擎
│   │   ├── vector_store.py          # FAISS + BGE-M3
│   │   ├── graph_store.py           # Neo4j 操作封装
│   │   └── graphrag.py              # 双引擎融合查询
│   ├── tools/                       # 工具层
│   │   ├── arxiv.py                 # Arxiv API
│   │   ├── semantic_scholar.py      # Semantic Scholar API
│   │   ├── pdf_parser.py            # PDF 解析 (PyMuPDF)
│   │   └── sandbox.py               # Docker 代码沙箱
│   ├── safety/                      # 安全与幻觉防线
│   │   ├── hallucination.py         # 六道幻觉防线
│   │   ├── guard.py                 # LLM Guard 输入过滤
│   │   └── citation.py              # 引用溯源链
│   ├── workflows/                   # LangGraph 工作流
│   │   ├── state.py                 # ResearchState 定义
│   │   ├── supervisor_graph.py      # Supervisor 主图
│   │   └── subagent_graphs.py       # 各子Agent子图
│   ├── api/                         # FastAPI 接口层
│   │   ├── routes.py                # REST 路由
│   │   ├── schemas.py               # Pydantic 模型
│   │   └── middleware.py            # RBAC + 限流中间件
│   ├── observability/               # 可观测性
│   │   └── tracing.py               # OpenTelemetry 配置
│   └── main.py                      # 应用入口
├── tests/                           # 测试
│   ├── unit/
│   ├── integration/
│   └── evaluation/                  # RAGAS 评估脚本
├── docker/
│   ├── Dockerfile
│   ├── sandbox.Dockerfile           # 代码沙箱镜像
│   └── grafana/                     # Grafana 仪表板 JSON
├── docker-compose.yml               # Neo4j + Redis + App + Grafana
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 三、分阶段实施计划

### Phase 1：核心 MVP（预计 3 周）—— 底座搭建

**目标**：Retriever + KG Builder + 基础问答 + 一键部署

| 步骤   | 任务               | 关键实现                                                                                                                      |
| ---- | ---------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 1.1  | 项目脚手架            | `pyproject.toml`、`requirements.txt`、目录创建、`docker-compose.yml`（Neo4j + Redis）                                              |
| 1.2  | 核心配置与LLM工厂       | `core/config.py` 读取 YAML 配置（API keys、模型路由表、超参）；`core/llm_factory.py` 实现多模型路由：qwen-max→Supervisor，qwen3.8-flash→轻量任务       |
| 1.3  | Agent基类          | `agents/base.py`：统一熔断器（3次失败→降级）、LangGraph Checkpointer审计日志、置信度字段                                                          |
| 1.4  | ResearchState定义  | `workflows/state.py`：TypedDict 包含 session_id、current_phase、task_queue、各阶段产出、confidence_scores、citation_chain、safety_flags |
| 1.5  | Retriever Agent  | Arxiv/Semantic Scholar API检索 → PDF下载解析(PyMuPDF) → FAISS+BGE-M3向量化存储 → 返回PaperSummary列表                                    |
| 1.6  | KG Builder Agent | 实体抽取(Prompt Engineering) → 关系三元组 → Neo4j Cypher写入 → 返回图谱子图JSON                                                            |
| 1.7  | GraphRAG双引擎查询    | `knowledge/graphrag.py`：向量检索Top-K + 图谱邻域扩展 → 融合排序 → 带引用源的Answer                                                           |
| 1.8  | Supervisor主图     | `workflows/supervisor_graph.py`：意图识别节点 → 条件路由 → 子Agent节点 → 结果聚合节点 → 质量门禁节点                                                |
| 1.9  | FastAPI接口        | `POST /api/query` 问答接口、`GET /api/session/{id}` 状态查询                                                                       |
| 1.10 | 基础测试             | `tests/unit/` 各Agent单元测试，mock LLM调用                                                                                       |

**交付物**：`docker-compose up` 后可进行文献问答，每个回答带引用溯源。

### Phase 2：数据分析闭环（预计 2 周）

**目标**：Data Analyst + Docker沙箱 + 自动Debug

| 步骤  | 任务                  | 关键实现                                                                                                               |
| --- | ------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 2.1 | Docker代码沙箱          | `tools/sandbox.py`：拉取 `sandbox.Dockerfile`（Python+pandas+scipy+matplotlib），安全配置（网络隔离、内存512MB、CPU 1核、系统调用白名单、超时30s） |
| 2.2 | Data Analyst Agent  | 接收实验数据 + 分析意图 → LLM生成代码 → 沙箱执行 → 捕获stdout/stderr/图片 → 若报错→LLM分析错误→修复重试（最多3次）                                       |
| 2.3 | Experiment Designer | 基于文献结论 + 数据趋势 → 生成实验假设 → 推荐实验方案（变量、对照组、统计方法）                                                                       |
| 2.4 | Supervisor集成        | 在Supervisor图中新增数据分析路由分支：用户上传CSV/Excel → 路由到Data Analyst → 结果回传                                                     |

**交付物**：用户上传数据文件后可自动分析并生成图表，失败时自动Debug重试。

### Phase 3：实验与写作（预计 2 周）

**目标**：Experiment Designer完善 + Academic Reviewer

| 步骤  | 任务                      | 关键实现                                                                        |
| --- | ----------------------- | --------------------------------------------------------------------------- |
| 3.1 | Experiment Designer完善   | 接入历史实验数据库 → 对比分析 → 冲突检测（文献A说X，文献B说Y）→ 主动指出矛盾并推荐验证方案                         |
| 3.2 | Academic Reviewer Agent | 论文草稿输入 → 结构检查(IMRaD) → 逻辑一致性 → 引用完整性验证 → 数据与结论一致性 → 格式合规（APA/MLA/GB/T 7714） |
| 3.3 | 写作辅助                    | 基于知识图谱的自动文献综述生成 → 引用自动格式化 → 实验方法描述生成                                        |
| 3.4 | Supervisor五环节全通         | 打通"找→读→算→写→审"完整链路，支持端到端科研流程                                                 |

**交付物**：可输入论文草稿进行自动审稿，输出修改建议。

### Phase 4：幻觉防线（预计 1 周）

**目标**：六道幻觉防线 + 置信度系统

| 步骤  | 任务          | 关键实现                                                                                                                             |
| --- | ----------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 4.1 | 六道防线实现      | `safety/hallucination.py`：①检索限定(仅基于检索到的文献) ②引用锚定(每个claim绑定source) ③KG反验(图谱事实校验) ④自一致性(3次采样投票) ⑤置信度评分(0-1分，<0.6标记) ⑥人工熔断(高风险结论暂停) |
| 4.2 | 引用溯源链       | `safety/citation.py`：每个输出claim→source文献→原文段落→页码，完整审计链                                                                            |
| 4.3 | LLM Guard集成 | 输入过滤(敏感词/PII) + 输出安全审查                                                                                                           |
| 4.4 | RBAC + 限流   | `api/middleware.py`：API Key认证 + 每分钟60次限流                                                                                         |

**交付物**：每个输出带置信度评分和完整引用链，低置信度自动标记。

### Phase 5：可观测 + 评估（预计 1 周）

**目标**：OpenTelemetry + Grafana + RAGAS CI门禁

| 步骤  | 任务              | 关键实现                                                                                  |
| --- | --------------- | ------------------------------------------------------------------------------------- |
| 5.1 | OpenTelemetry集成 | `observability/tracing.py`：每个Agent的LLM调用、工具调用、状态变更全链路追踪 → Jaeger                      |
| 5.2 | Prometheus指标    | 请求量、延迟P50/P95/P99、错误率、Token消耗、缓存命中率                                                   |
| 5.3 | Grafana仪表板      | 预设Dashboard JSON：Agent调用链拓扑图、成本面板、质量面板                                                |
| 5.4 | RAGAS评估         | `tests/evaluation/`：Faithfulness、AnswerRelevancy、ContextRecall、ContextPrecision 自动化测试 |
| 5.5 | 金标集构建           | 50条科研问答对 + 人工标注正确答案 → CI门禁基准                                                          |

**交付物**：Grafana实时监控面板 + CI门禁脚本。

### Phase 6：打磨与答辩（预计 1 周）

**目标**：性能优化 + 演示 + 文档

| 步骤  | 任务                                     |
| --- | -------------------------------------- |
| 6.1 | 性能优化：缓存预热、批量Embedding、Neo4j查询优化        |
| 6.2 | 消融实验：按方案附录B执行5组对比实验，生成对比图表             |
| 6.3 | 演示脚本：准备3个端到端演示场景（文献调研链路、数据分析链路、论文审稿链路） |
| 6.4 | 文档完善：README、API文档、部署手册、答辩PPT素材         |

---

## 四、关键接口/类型定义

### ResearchState（LangGraph 全局状态）

```python
class ResearchState(TypedDict):
    session_id: str
    user_id: str
    research_topic: str
    current_phase: Literal["literature", "knowledge_graph", "experiment", "writing"]
    task_queue: list[Task]
    completed_tasks: list[Task]
    literature_results: list[PaperSummary]
    kg_snapshot: str | None
    experiment_results: ExperimentReport | None
    writing_draft: str | None
    confidence_scores: dict[str, float]
    citation_chain: list[Citation]
    safety_flags: list[SafetyAlert]
    human_review_required: bool
```

### Agent 基类接口

```python
class BaseAgent:
    name: str
    model: str
    circuit_breaker: CircuitBreaker  # 3次失败→降级
    async def execute(self, state: ResearchState) -> ResearchState: ...
    async def audit_log(self, action: str, detail: dict): ...
```

### API 路由

- `POST /api/session` — 创建研究会话
- `POST /api/session/{id}/query` — 主问答入口（Supervisor自动路由）
- `POST /api/session/{id}/analyze` — 上传数据进行分析
- `POST /api/session/{id}/review` — 提交草稿进行审稿
- `GET /api/session/{id}` — 查询会话状态和中间产出
- `GET /api/session/{id}/citation-chain` — 查看引用溯源链
- `GET /api/health` — 健康检查

---

## 五、测试策略

| 层级   | 工具                      | 覆盖目标                                       |
| ---- | ----------------------- | ------------------------------------------ |
| 单元测试 | pytest + pytest-asyncio | 每个Agent的核心逻辑、工具函数、安全模块                     |
| 集成测试 | pytest + docker         | Agent间协作、Neo4j/Redis连接、沙箱执行                |
| 评估测试 | RAGAS                   | Faithfulness、AnswerRelevancy、ContextRecall |
| CI门禁 | GitHub Actions          | 每次PR自动运行单元测试 + RAGAS评估（阈值>0.7）             |

---

## 六、假设与默认决策

1. **LLM API**：假设使用通义千问API（qwen-max/qwen3.8-flash），配置通过环境变量注入
2. **Neo4j**：通过Docker运行社区版，本地开发无需企业版功能
3. **PDF解析**：使用PyMuPDF（轻量、快速），不支持扫描版PDF的OCR（可后续扩展）
4. **中文优化**：Embedding模型选择BGE-M3（原生支持中英双语），检索和生成均以中文为主
5. **代码沙箱安全**：Docker容器网络隔离、内存512MB上限、超时30秒——这是竞赛可验证的安全基线
6. **配置管理**：使用YAML文件 + 环境变量覆盖，不引入etcd/consul等外部配置中心
7. **单用户模式**：Phase 1-3先实现单会话单用户，Phase 5再加RBAC多用户

---

1.download BAAI/bge-m3

2.前端开发：功能（全部真实调用后端 11 个接口，无假数据） ：健康状态轮询、会话问答、文献/引用链、知识图谱可视化、CSV 上传+数据分析、实验设计、论文写作、学术审阅、参考文献格式化、右侧可解释 Inspector（phase/置信度/耗时/错误）。

设计 ：深墨底 + 青碧强调色（无紫渐变）、Spectral 衬线标题 + IBM Plex Sans/Mono、渐变网格 + 噪点纹理、左侧分组导航 + 非对称主区 + 可折叠 Inspector、首屏错峰淡入、置信度仪表滚动、图谱加载动画。字体全部本地打包（不依赖 Google Fonts）。

3.## 修复与验证

### ① 刷新丢会话（前端）

- session.ts ：sessionId 持久化到`localStorage` ； 404 自动恢复 （继续提问遇到会话失效会自动新建，不再报错）； App.vue 启动时回填会话状态与引用链。

- 验证：整页刷新后会话 id、研究主题、文献数、置信度全部恢复；后端重启后的过期会话被自动清理并重建。
  
  ### ② 响应慢（后端）

- kg_builder/agent.py ：逐篇 串行 LLM 抽取改为受信号量约束的 并发 ；抽取篇数收敛到 4 篇；摘要截断到 1200 字符。

- graph_store.py ：实体/关系按类型分组用`UNWIND` 批量写入 （原来每个实体/关系一次 Cypher 往返）。

- graphrag.py ：KG 实体检索由「逐个关键词循环查询」改为 一次批量查询 。

- 验证：单次问答 ~46s （此前 60–73s）。
  
  ### ③ 图谱没有连线（前端）

- 根因：后端 citation-chain 每条 claim 的来源放在`source` 字段，前端只读了`citations/evidence/sources` 。

- session.ts 新增`claimCitations()` 读取`c.source` 。

- 验证：知识图谱 引用边 0 → 23 ；文献页每条结论显示出真实“支撑证据”（标题/作者/年份/链接），平均置信度 83.4%。
  
  ### ④ /analyze 顺带跑实验设计（后端）
  
  routes.py 分析/上传阶段改为`data_analysis` ； supervisor_graph.py 增加`data_analysis → data_analyst → END` 。

- 验证：单元测试 + 端到端（70.9s，只跑分析，无 design）

4.## ① KG 抽取不稳定 → 已根治

真正根因 ：`qwen3.8-flash` 是 推理型模型 ，默认会先生成一大段隐藏思考（`reasoning_content` ）。实测抽取一次要烧掉 ~1900 token（其中 1546 是思考），经常把 4096 的预算耗尽 → 正文被截断/为空 → JSON 解析失败。

修复 ： base.py 的`_call_llm` 新增`json_mode` 参数，抽取时同时启用严格 JSON 模式并 关闭隐藏思考 ：

`llm = llm.bind(
    response_format={"type": "json_object"},
    extra_body={"enable_thinking": False},
)`

kg_builder/agent.py 抽取时以`json_mode=True` 调用。

效果（浏览器实测） ：

- 单篇抽取 ~10s+ → ~4s （219 token，思考 0）

- 「知识图谱」置信度 0% → 100% （4/4 全部成功，日志无一条抽取失败）

- Neo4j 15 节点/17 关系 → 38 节点/267 关系

- 兜底的容错解析（截取`{...}` ）保留着作为二次保险
  
  ## ② 对话记录持久化 → 已完成
  
  session.ts ：

- `messages` 一并写入 localStorage（保留最近 20 条），启动时恢复；

- 「重置会话」以及**会话失效（404）**时一起清空，避免旧会话记录串到新会话里；

- 修了一个副作用：404 自动重建时不再中途清空 sessionId，否则视图会瞬间跳回「建立会话」表单。
  效果（浏览器实测） ：整页刷新后「对话记录」保留 4 条 ，会话 id、研究主题、置信度同时恢复。

如果还想把 81s 压下来，剩下的主要开销在「检索 20 篇 + BGE-M3 本地向量化」，需要的话我可以针对性优化。

## 为什么之前没有图

三个环节都是断的，缺一不可：

1. 沙箱 ：容器用`--rm` +`--tmpfs /workspace` 跑，脚本按提示把图画到`/workspace/output.png` —— 容器一退出 tmpfs 就销毁 ，文件根本出不来；
2. 后端 ：`/analyze` 只返回`stdout` 文本，`QueryResponse` 里连图片字段都没有；
3. 前端 ：结果区只渲染文本。

## 顺带修掉的两个 bug（都在拖累这个演示）

- 分析在用假数据 ：结论里是「300 样本、组 A/B/C、温度 20–40℃」，和你这份 CSV 完全不符。根因是 state.py 的`ResearchState` 没声明`data_file_path` ，而 LangGraph 会丢弃未声明的键 → 分析智能体以为没数据，走了「自己造样本」的分支。已补上`data_file_path / writing_section / citation_style` 。

- 代码生成偶发返回空 ：`qwen3.8-flash` 是推理模型，隐藏思考会吃光 token 预算导致正文为空（沙箱跑了空脚本）。给`_call_llm` 加了`enable_thinking` 开关，代码生成关闭思考——耗时也从 60–86s 降到 ~30s 。
  
  ## 中文字体
  
  沙箱镜像原本只有 DejaVu（无中文字形），图里中文会变方框。已在 sandbox.Dockerfile 加`fonts-wqy-zenhei` （放在 pip 层之后以复用缓存）并重建镜像，实测保存图片 零缺字警告 。

10/6

- [x] 前端设计优化
- [x] 登录注册功能设计，演示时不需要登录/未登录限制5个问题
- [x] 数据持久化
- [x] 沙箱安全：docker 不可用时拒绝执行 AI 生成代码（A1，见 NEXT_PLAN）

> **后续待办不再在此罗列**。经 2026-10 全面代码走查后，三阶段开发计划（止血与基线 / 真壁垒打穿 / 产品收敛交付）已统一收敛到 **[`NEXT_PLAN.md`](./NEXT_PLAN.md)**，含逐项验收标准与"明确不做"清单。

## 1. 数据来源：免费高质量源 + 垂直定位

**英文 STEM 领域，免费源已经够用，不需要爬 Google Scholar：**

| 数据源                      | 覆盖                            | 免费方式                         | 推荐度   |
| ------------------------ | ----------------------------- | ---------------------------- | ----- |
| **arXiv API**            | CS / 物理 / 数学 /stat            | 完全免费，无 key                   | ★★★★★ |
| **Semantic Scholar API** | 全域，带引用图谱                      | 免费 tier（100 req/5min），留邮箱加额度 | ★★★★★ |
| **OpenAlex**             | 2.5 亿作品，替代 Microsoft Academic | 完全免费，polite pool 加邮箱即可       | ★★★★★ |
| **Crossref + Unpaywall** | DOI 元数据 + 合法 OA PDF 链接        | 免费                           | ★★★★  |
| **PubMed / Europe PMC**  | 生物医学                          | 免费                           | ★★★   |
| **CORE**                 | OA 论文聚合                       | 免费注册                         | ★★★   |

**不要碰的**：Google Scholar 无官方 API，爬取会被封 IP；CNKI / 万方 / 维普没有合法免费 API，版权风险高。

**垂直 vs 通用**：做通用科研 Copilot，前三个（arXiv + S2 + OpenAlex）就够了。中文文献让用户自己上传 PDF 最稳。

---

## 2. Retriever Agent 深度功能设计

```
每篇论文自动抽取：
├─ 研究问题（一句话）
├─ 核心方法（含基线对比）
├─ 数据集 / 样本规模
├─ 核心结果（关键指标数值）
├─ 局限性（作者自己承认的 + 你标注的）
└─ 与本课题相关性（1-6 分，附理由）
```

**竞赛加分功能**（方案里已有方向，落地时做出来）：

- **文献矩阵**：N 篇 × M 维度（方法 / 数据 / 指标 / 年份）的对比表，Elicit 那套，支持导出 CSV
- **矛盾检测器**：自动标 "1 篇说 X，另 1 篇说 Y" 的分歧点（Consensus 风格）
- **研究空白识别**：KG 里哪些节点连边稀疏 = 没人做过
- **自动 Debug 闭环**：PDF 解析失败 → Grobid → PyPDF2 → OCR 三级降级
- **字段绑定原文 span**：每个抽取字段标注在 PDF 第几页第几段，可点击溯源（这是对抗幻觉的关键）

---

## 3. MCP / 技能接入

**完全可以，而且推荐用 MCP 而不是自己写爬虫**：

- **官方 / 社区 MCP server**：已经有 `mcp-arxiv`、`mcp-semantic-scholar`、`mcp-zotero`、`mcp-pubmed`，装了就能用
- **自定义 MCP**：如果要接 CNKI，写一个薄 wrapper 调机构 VPN 内的接口（仅限本校 IP）

**竞赛架构建议**：把数据源做成 MCP tool 而不是硬编码在 Retriever 里 —— 这样以后加一个新源只需要加一个 MCP server，不改 Agent 代码。

---

## 4. 用户自建知识库（必须做，而且是核心卖点）

**这恰恰是方案里 "以课题组文献库为知识底座" 的落地方式，不是 fallback，是一等公民**：

```
用户上传 PDF 流程：
PDF → Grobid 解析（章节/公式/表格）→ 切块
    → BGE-M3 向量化 → FAISS 索引
    → NER 抽实体 → 写入 Neo4j
    → 与公开库检索结果合并去重
```

**为什么这是加分项而不是妥协**：

- CNKI / 万方拿不到合法 API → 用户自己导 PDF 上传，绕开版权
- 课题组内部讲义、实验记录、学位论文不公开 → 只有自建库能覆盖
- 评委老师自己就有一堆 PDF，现场上传一个立刻能 demo，比讲 API 更有说服力

**原型里已经有这个入口**：优化工作台里文件上传接口， "上传 PDF 到课题组库"，和文献库视图联动。

---

**一句话总结**：英文用 arXiv + Semantic Scholar + OpenAlex 三个免费 API 就够；中文和内部资料走用户上传自建库；数据源用 MCP 接入便于扩展；深度处理按固定 Schema 抽取 + 文献矩阵 + 矛盾识别做。

去掉 Grobid 层与 TEI 转换 → 两级链 **PyMuPDF 主路 → OCR 兜底**（原 Grobid 的质量阈值逻辑转给 OCR：文字稀疏**或乱码率 >0.3** 且 rapidocr 可用时触发）

在研析通 v2.0 里，**图谱构建不是“把论文关系画出来”的可视化附属品，而是整个系统的事实底座、推理骨架和幻觉校验器**。它的作用可以概括为：

**1. 构建领域知识底座**  
把 Retriever 产出的 `PaperSummary` 通过 NER + RE 抽成实体和关系，增量写入 Neo4j，形成课题组可积累、可复用的知识图谱。节点用 `title_hash + arxiv_id` 联合主键去重，批量 MERGE，保证幂等和效率。

**2. 支撑 GraphRAG 双引擎**  
FAISS 向量负责“语义找入口”，知识图谱负责“沿关系补上下文”。图谱构建让系统能做多跳关系推理，例如：`论文 → 方法 → 数据集 → 指标 → 对比方法`，弥补纯向量 RAG 不擅长关系推理的短板。

**3. 作为幻觉校验器**  
这是方案里最关键的定位：**图谱不是 RAG 的配角，而是事实校验器**。对 LLM 输出中的 `(方法, 数据集, 指标)` 三元组，在 KG 中反向查找验证，不一致就标记 `Hallucination_Flag`。关系抽取也受 Schema 约束，只允许预定义 8 种关系类型，并做反向验证和抽样人工复核。

**4. 支撑上层 Agent 决策**  
Experiment Designer 的每条建议必须引用 KG 中具体 `Paper` 节点，含 Arxiv ID，无引用不出建议；Academic Reviewer 做引用溯源；Retriever 结果也与图谱关联。图谱让建议有据可查，而不是模型自由发挥。

**5. 产出研究洞察**  
KG Builder 的输出不只是图谱，还包括：**研究空白分析报告、技术演进路线图**。它可以帮助发现文献矛盾、提出研究假设、推荐实验方向。

**6. 提供可解释证据链**  
每条结论可以沿图谱路径追溯到论文节点和原文段落，用于推理轨迹面板、证据链面板和审计日志，满足科研场景对可追溯、可审计的要求。

**7. 实现课题组知识复用**  
图谱持续积累后，新学生加入即可继承前人整理的文献关系、方法脉络和研究经验，让科研经验可复用。

一句话总结：**图谱构建把非结构化文献转成可查询、可推理、可验证的关系网络，是 KG×RAG 双引擎的事实底座和抗幻觉核心。**

而是 **本体驱动、证据锚定、增量融合、图谱分析** 的科研洞察引擎，负责把碎片文献变成可查询、可推理、可验证、可发现空白的领域知识网络。

**一句话总结**：优化后的 Data Analyst Agent 不再只是“生成代码并跑通”，而是 **RAG 知识增强、沙箱安全执行、自动 Debug、出版级可视化、全链路可复现** 的数据科学助手，让科研数据处理从“能跑”升级到“可信、可查、可发表”。

**一句话总结**：优化后的 Experiment Designer Agent 不再只是“对比 SOTA 给建议”，而是 **证据驱动、可搜索、可验证、可执行、可闭环** 的实验方案优化引擎，让每条建议都能追溯到论文、经得起验证、落得了地。

**Agent 4 要实现的是：从“当前实验配置”出发，结合 KG 中的 SOTA 证据，自动诊断瓶颈、生成候选方案、多目标优化、可行性校验、输出可执行配置和验证计划，并把实验结果回流 KG 形成闭环。**  
**效果是：每条建议都能追溯到论文、经得起验证、落得了地，让科研实验设计从“凭经验试错”升级为“证据驱动、可复现、可进化”的优化过程。**
