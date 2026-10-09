# 研析通 v3.0 — 后续开发计划

> 本文件基于对 `src/`、`docs/`、`docker-compose.yml` 的逐行走读后撰写。
> 原则：**Less structure, more intelligence**；不堆技术，把真壁垒打穿；小切口、真痛点、有 Before/After。
> 制定日期：2026-10-09。

---

## 0. 总诊断（一句话）

当前系统是 **"7 个 Agent 的壳子 + 3 个真本事的内核"**：

- 架构图上吹的东西（任务分解、Supervisor 调度、六道防线）一半是死代码或玩具实现；
- 真正有壁垒的（三元组事实反查、Data Analyst 九段流水线、领域包分区）反而被稀释；
- 没有一个数字证明它比通用大模型强（无金标集、无 RAGAS 基线、无 Before/After）。

**接下来不是加东西，是砍假结构、把真壁垒打穿、拿到可量化的 Before/After。**

---

## 1. 现状对标差距（带代码证据）

### 1.1 "Less structure, more intelligence" —— 当前最大问题

| 现状（代码证据） | 性质 |
|---|---|
| `src/workflows/supervisor_graph.py:239 route_intent` 是**关键词表路由**：`if "写论文" in query.lower()` | 假智能 |
| `src/agents/supervisor/agent.py:40` 真调了 LLM 做 intent 分类，但结果**只写审计日志，从未被路由读取**——LLM 意图识别是死代码 | 炫架构 |
| `src/workflows/state.py:115` 定义了 `task_queue / completed_tasks`，全 `src/` grep 只有 state.py 自己写过，**没有任何节点读写** | PPT 架构 |
| 7 个 Agent 并非 Supervisor 动态调度，而是 48 个路由按 `current_phase` 硬绑：`/analyze→data_analyst`、`/design→experiment_designer`……LangGraph 主图实际只在 `/query` 一条路径上跑 | 名义多智能体 |
| Supervisor 自己不做任务分解/冲突仲裁，就是个 GraphRAG 问答器 + 事后跑门禁 | 名不副实 |

### 1.2 六道幻觉防线 —— 4 道是计数器，1 道是真壁垒

逐行看 `src/safety/hallucination.py`：

| 层 | 实现 | 问题 |
|---|---|---|
| L1 检索范围 | `claim.split()` 取 4 字母以上英文单词做子串重叠 | **中文整句被 split 成一个 token，中文输入下评分≈掷硬币** |
| L2 引用锚定 | 正则数 `[n]` 个数 ÷ (claims/2) | 数的是方括号格式，不是引用是否真支撑该 claim |
| L3 KG 反验 | 退化分支 = 提到的实体数 / 实体总数 | **分子分母方向反了**：回答越像念实体名单分越高 |
| L4 自一致性 | 反义词对同时出现扣 0.15 | 玩具 |
| L6 人工熔断 | 5 条中英正则 | 聊胜于无 |
| **真壁垒** | `src/safety/triple_check.py`（方法/数据集/指标三元组反查 Neo4j） | 唯一经得起评委追问的层 |

### 1.3 检索优化

现状：三源（arXiv / Semantic Scholar / OpenAlex）并行 → LLM enricher 打一次分 → top20 编码 FAISS → `top_k=8`。

差距：
- **没有 cross-encoder rerank**。从 ~60 篇候选到塞进 context 的 8 篇，只靠一次 LLM 粗排。本地加 `BAAI/bge-reranker-v2-m3`（与 BGE-M3 同生态），CPU 上 60 篇 rerank 约 2-3 秒，对忠实度提升是数量级的。
- **实体链接用 2/3/4-gram 暴力滑窗**（`src/knowledge/graphrag.py:46 _question_keywords`），"注意力机制"被切成"注意/意力/力机制…"撞名，误匹配高。应改成实体 embedding 召回。
- `ctx[:8000]` 硬截断，可能切在 `[3]` 引用编号中间。
- 无年份/引用数/venue 元数据过滤，无 HyDE，无 query 改写迭代。

### 1.4 安全防护 —— 有一个生产事故级漏洞

`src/tools/sandbox.py:311 get_sandbox()`：docker daemon 不可用时**静默 fallback 到 MockSandbox，在宿主机直接 `subprocess.run` 执行 LLM 生成的任意 Python 代码**。演示机 docker 一停，AI 生成的代码就在笔记本上裸奔。**必须第一周修掉。**

其他：
- CORS `allow_origins=["*"]`（`src/main.py:57`）
- MySQL / Neo4j / Grafana 全是弱默认密码写死在 `docker-compose.yml` / `config.yaml`
- 语义缓存 key = system_prompt+prompt 全文，**幻觉判定、意图识别也走共享缓存**，多轮状态变化可能命中脏缓存
- Jaeger `SPAN_STORAGE_TYPE=memory`，重启 trace 全丢

### 1.5 工程化

- `MemorySaver` 当 LangGraph checkpointer，进程重启图状态即丢；会话又靠 SQLite `session_store` 重建——**两套状态存储并存，边界模糊**。
- 熔断器每个 Agent 一个，但 LLM / Neo4j / 沙箱失败全混在一个 breaker 里，且全项目没人 `set_fallback`。
- `trace_id = uuid4()[:8]` 每个节点新生成，不跨节点传播——前端"链路追踪"实际是 SQLite 按 session_id 串的日志，不是真 OTel trace。
- 无 CI（`.github/workflows` 不存在）。

### 1.6 差异化 / Before-After —— 最要命的短板

真壁垒候选已存在但没被量化：
- Data Analyst 九段流水线（画像 → RAG 召回 → 规划 → codegen → 沙箱 → 自动 debug → 校验 → 报告 → 打包 Notebook+环境锁）
- triple_check 三元组反查
- 领域包 kb 分区 + 跨域降权（最新提交 `36b41dd`）

但**没有一个数字证明它比通用大模型强**：
- 没有金标集（PLAN.md 说 50 条 QA，`tests/evaluation/` 里只有脚本，未见 gold set）
- RAGAS 基线分数未知；README 里"忠实度 83.4%"是单次演示截图，不是回归基线
- 没有 token 成本 / 延迟的实测对比
- "一句话帮谁解决什么"还是"AI 科研合伙人"——太大

**收敛切口**：**"研究生 3 分钟完成一次可溯源的文献调研"**——输入一个研究问题，产出：结构化文献矩阵（方法/数据/指标/年份对比表）+ 文献矛盾点 + 研究空白 + 每条结论点回原文段落。数据分析/实验设计/写作审稿作为这条主线上的自然延伸，不再 7 个视图平铺。

Before/After 实证表：

| 指标 | 手动调研 | 直接问 DeepSeek/ChatGPT | 研析通 |
|---|---|---|---|
| 20 篇文献结构化笔记耗时 | 2-4 小时 | 2 分钟但无引用 | 3 分钟带引用 |
| 引用可回原文率 | — | ~10%（瞎编） | 目标 100%（triple_check + span 锚定） |
| 文献矛盾发现 | 靠读 | 发现不了 | 自动标出 |
| 单问题 token 成本 | — | 实测填入 | 实测填入 |

### 1.7 文档一致性问题

- 版本号打架：README 写 v2.0，`src/main.py` FastAPI `version="3.0.0"`，`pyproject.toml` `version="2.0.0"`，`config.yaml` `version: "2.0.0"`，代码注释里 Data Analyst 自称 v3.0、supervisor_graph 自称 v3.0。
- `docs/研析通v2.0_完整方案.md` 描述的技术栈与实际不符：写了 DeepSeek-V3 / qwen-plus / qwen-turbo / Celery / Elasticsearch / Zotero API / LLM Guard / 阿里云内容安全 / RBAC 三级 / ELK / K8s，实际代码用的是 qwen-max / qwen3.8-flash / deepseek-v4-pro / qwen3-coder-flash，**没有 Celery、没有 ES、没有 Zotero、没有 ELK**。
- README 吹"Supervisor：意图识别·任务分解·资源路由·冲突仲裁·质量门禁"，实际只有质量门禁是真的。
- PLAN.md 末尾挂着一堆未打勾的待办，与代码实际进度脱节。

---

## 2. 后续开发计划（三阶段，约 7 周）

### Phase A：止血与基线（第 1-2 周）—— 先别加功能

| # | 动作 | 验收标准 |
|---|---|---|
| A1 | **修沙箱降级洞**：docker 不可用时直接报错 503，禁止回退 MockSandbox 执行宿主代码；MockSandbox 仅在 `pytest` 时显式启用 | 断 docker 后 `/analyze` 返回明确错误，宿主无任意代码执行 |
| A2 | **砍假结构**：删 `task_queue/completed_tasks` 死字段；`route_intent` 真接 Supervisor 的 LLM intent（置信度 <0.6 才回退关键词） | 路由由 LLM intent 驱动，日志能看到 intent→edge 决策链 |
| A3 | **文档对齐**：版本号统一到 v3.0；`研析通v2.0_完整方案.md` 改成"目标态 vs 现状"两栏，删除代码里不存在的组件（Celery/ES/Zotero/ELK/K8s/RBAC 三级）；README 重写为"一句话定位 + 3 个 demo + 1 张对比表"；PLAN.md 末尾待办清单替换为本计划 | `grep -r "v2.0" docs README.md` 结果与代码版本一致；方案文档不出现未实现组件名 |
| A4 | **建金标集**：30-50 条真实科研 QA（覆盖中文问题、英文问题、库外问题、数字类问题），人工标注"正确答案 + 应引用论文" | `tests/evaluation/goldset.json` 入库，CI 可跑 |
| A5 | **跑通 RAGAS + 消融基线**：faithfulness / answer_relevancy / context_recall / context_precision 跑出来存档；消融：关掉 triple_check、关掉 KG 融合各跑一次 | `docs/baseline.md` 有一张基线表，后续每次改动对照 |
| A6 | 安全基线：CORS 收敛到前端域名；compose 弱密码全部 env 注入；补 `.env.example` | `grep -rn "yanxitong/yanxitong" config.yaml docker-compose.yml` 查不到硬编码密码 |

### Phase B：把真壁垒打穿（第 3-5 周）—— 不堆新 Agent

| # | 动作 | 验收标准 |
|---|---|---|
| B1 | **检索 rerank**：接入 `bge-reranker-v2-m3`，vector top-20 之后精排取 top-8；entity linking 从 n-gram 滑窗换成 embedding 召回 | RAGAS context_precision 相对 A5 基线提升 ≥15% |
| B2 | **幻觉防线真做**：L1 改 claim-source 余弦相似度（复用本地 BGE-M3）；L2 改"claim 是否被其标注 citation 的原文 span 支撑"（qwen-flash 一次 NLI 判定，批量并发）；L4 改数字一致性（回答数值 vs KG edge.value 容差）；保留 triple_check 主路 | 每条前端可见 claim 可点开看"原文片段 + 支撑判定分"；库外问题正确拒答率 100% |
| B3 | **证据 span 锚定**：PDF 入库按段落存 `(paper_id, page, paragraph_idx, text)`；回答里 `[n]` 引用对应到具体段落而不只是标题 | `/citation-chain` 返回 page+paragraph，前端可跳原文 |
| B4 | **Context smart truncate**：按引用编号完整性切，不切 `[n]` 中间；超长时按 rerank 分数淘汰尾部 chunk | 长 context 下无悬空引用 |
| B5 | **Data Analyst 可复现性做扎实**：Notebook 导出 + 环境锁 + 代码 + 图 + 报告五件套已存在，补"一键 rerun"接口 | 评委上传 CSV 后下载 zip，解压跑 `make rerun` 复现同一张图 |
| B6 | **反馈闭环**：前端每条回答旁加"赞/踩/引用不准"按钮，落 SQLite；新增 `POST /feedback` + `GET /feedback` 查询页 | 接口可调，反馈可导出为微调样本 CSV |

### Phase C：产品收敛与交付（第 6-7 周）

| # | 动作 | 验收标准 |
|---|---|---|
| C1 | **前端收敛主线**：首屏就是"输入研究问题 → 3 分钟出文献工作台"；7 个视图收敛为 3 个（调研工作台 / 数据分析 / 论文审阅），其余收进二级页 | 3 分钟 demo 故事线不跳页 |
| C2 | **Before/After 实证报告**：用金标集跑出"手动 vs 通用大模型 vs 研析通"三方对比表（耗时/引用准确率/矛盾发现数/token 成本） | `docs/demo_metrics.md`，答辩 PPT 直接用 |
| C3 | **可观测落地**：trace_id 跨节点传播进 OTel span；Grafana 面板预设"每问题成本 / 延迟 P95 / 幻觉拦截数" | Grafana 打开就能看到这三个数 |
| C4 | **部署加固**：app 服务加健康检查 + 优雅退出；`docker compose up` 一条命令起全栈；README 重写 | 新机器 clone 下来 10 分钟跑起来 |
| C5 | **CI 最小可用**：`.github/workflows/ci.yml` 跑 unit 测试 + RAGAS 金标集门禁（faithfulness <0.8 报警） | PR 触发，红了不能合 |

---

## 3. 明确不做的事（守住"小切口"）

- **不加第 8 个 Agent**（写作润色、翻译等）
- **不上 Milvus/Qdrant**——FAISS 单文件对当前规模够了
- **不做 RBAC 细粒度权限**——演示阶段账号隔离够用
- **不做 CNKI/万方爬虫**——版权风险，用户上传 PDF 自建库是对的
- **不堆第 7-12 道幻觉防线**——把现有 6 道里 4 道玩具换成真的，比加新层有用
- **不引入 Celery / Elasticsearch / ELK / K8s**——compose + SQLite 足够跑到答辩

---

## 4. 启动顺序

按依赖关系：

```
A1(沙箱洞) ──┐
A2(砍假结构) ─┼──> A4(金标集) ──> A5(基线) ──> B1/B2/B3 ──> C1/C2
A3(文档对齐) ─┘                                          │
A6(安全基线) ────────────────────────────────────────────┘
```

下一步从 **A1（沙箱降级洞）** + **A3（文档对齐）** 并行开始。
