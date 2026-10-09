# 研析通 v3.0

> **AI 科研合伙人** — 以课题组文献库与实验记录为知识底座，构建 "KG×RAG 双引擎 + 多智能体协作 + 全链路可观测" 的 AI 科研系统，覆盖 "文献调研 → 知识构建 → 数据挖掘 → 实验设计 → 论文合规" 五大环节。

## 🎯 一句话定位

**帮研究生在 3 分钟内完成一次可溯源的文献调研**：输入一个研究问题，产出结构化文献矩阵、文献矛盾点、研究空白，每条结论可点回原文段落。数据分析、实验设计、论文审稿是这条主线上的延伸能力。

> 辅助研究，不代替研究；辅助表达，不虚构内容。

## 🏗️ 系统架构（与代码一致）

```
┌──────────────────────────────────────────────────────────┐
│                  🎯 Supervisor Agent                      │
│  GraphRAG 上下文组装 · 答案生成 · 幻觉门禁 · 引用链       │
└──────┬───────────────────────────────────┬───────────────┘
       │                                   │
┌──────▼───┐  ┌────────────┐  ┌──────────▼──────┐  ┌──────────────┐
│ Retriever │  │ KG Builder │  │ Data Analyst    │  │ Experiment   │
│ (arXiv /  │→│ (Neo4j 实  │  │ (Docker 沙箱 +  │  │ Designer     │
│  S2 /     │  │  体/关系)  │  │  自动 Debug 闭环)│  │ (Optuna 多   │
│  OpenAlex)│  └────────────┘  └─────────────────┘  │  目标优化)   │
└──────────┘                                    └──────────────┘
       ┌──────────────┐  ┌──────────────────┐
       │ Writing Asst  │  │ Academic Reviewer │
       └──────────────┘  └──────────────────┘
       ──────────────── Tool & Resource Layer ────────────────
       BGE-M3 (本地) · FAISS · Redis 语义缓存 · MySQL 账号配额
       OpenTelemetry → Jaeger · Prometheus · Grafana
```

> 说明：Supervisor 不做任务分解/动态调度——各能力由独立端点显式触发（`/analyze`、`/design`、`/write`、`/review`），Supervisor 专注于问答路径的 RAG 生成与质量门禁。这是有意的"少结构、多智能"取舍，详见 `docs/NEXT_PLAN.md`。

## 🚀 快速开始

```bash
# 1. 配置 API Key
echo "DASHSCOPE_API_KEY=your_key" > .env

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动后端与依赖服务（MySQL 账号库映射到宿主 3309，避开本机 MySQL）
docker compose up -d

# 4. 启动前端工作台（开发模式，已代理 /api → 8001）
cd frontend && npm install && npm run dev   # http://localhost:4321

# 5. 验证
curl http://localhost:8001/api/health
```

> 未登录即可直接使用：匿名免费问答 5 次；注册 / 登录后以账号身份提问，研究数据保存到账号。

## 📡 API 参考

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/metrics` | Prometheus 指标 |
| `POST` | `/api/session` | 创建研究会话 |
| `POST` | `/api/session/{id}/query` | 文献问答 |
| `POST` | `/api/session/{id}/upload` | 上传数据文件 |
| `POST` | `/api/session/{id}/analyze` | 数据分析 |
| `POST` | `/api/session/{id}/design` | 实验设计 |
| `POST` | `/api/session/{id}/write` | 论文写作 |
| `POST` | `/api/session/{id}/review` | 论文审稿 |
| `POST` | `/api/session/{id}/bibliography` | 格式化参考文献 |
| `POST` | `/api/auth/register` | 注册账号，返回 Bearer 令牌 |
| `POST` | `/api/auth/login` | 登录，返回 Bearer 令牌 |
| `POST` | `/api/auth/logout` | 登出（吊销令牌） |
| `GET` | `/api/auth/me` | 当前身份与匿名配额 |
| `GET` | `/api/session/{id}` | 会话状态 |
| `GET` | `/api/session/{id}/citation-chain` | 引用溯源链 |

### 账号与配额（演示模式）

- 未登录可用：前端自动携带 `X-Anon-Id` 标识匿名身份，免费问答 5 次；仅「创建会话 / 继续提问」计数，分析、设计、写作、审稿、上传不计次。
- 登录身份：注册 / 登录后请求头携带 `Authorization: Bearer <token>`，以账号身份提问（不占用匿名配额）。
- 身份隔离：会话、知识库、向量索引与知识图谱均按身份隔离，不同用户及匿名身份的研究数据互不可见。

### 数据持久化

- 账号 / 令牌 / 匿名配额在 MySQL（宿主 3309），知识图谱在 Neo4j，语义缓存在 Redis（宿主 6380，均容器卷持久化）。
- 会话状态（历史会话 / 引用链）、论文向量索引、上传文件落盘于 `data/`（compose 挂载 `app-data` 卷），后端重启后自动恢复。

## 🛡️ 安全与质量门禁

| 层 | 名称 | 说明 |
|---|------|------|
| 1 | 检索范围限定 | 回答只基于检索到的文献片段与 KB 块 |
| 2 | 引用锚定 | 每个观点标注 `[n]` 引用来源 |
| 3 | **三元组事实反查** | LLM 输出的 (方法/数据集/指标) 在 Neo4j 中反向验证，硬冲突升级为高风险 |
| 4 | 自一致性 | 内部逻辑矛盾检测 |
| 5 | 置信度评分 | 加权聚合，低于 0.6 标记 |
| 6 | 高风险熔断 | 医疗断言/绝对化表述正则拦截 |

> 沙箱安全：Docker `--network none` + `--read-only` + tmpfs + 512MB/1CPU/30s。**Docker daemon 不可用时后端拒绝执行任何 AI 生成代码**（`SandboxUnavailableError`），绝不静默回退到宿主进程执行；仅 pytest 或显式 `YXT_ALLOW_MOCK_SANDBOX=1` 才允许 in-process 模式。

## 📊 可观测性

- **Jaeger**: `http://localhost:16686` — 全链路追踪
- **Prometheus**: `http://localhost:9090` — 指标采集
- **Grafana**: `http://localhost:3000` — 监控仪表板

## 🧪 测试

```bash
# 全部测试
pytest tests/ -v -o "addopts="

# RAGAS 评估
python -m tests.evaluation.ragas_eval
```

## 🏆 真壁垒（与通用大模型的差异点）

1. **三元组事实反查**：每条 (方法, 数据集, 指标) 断言在知识图谱中反向验证，不一致即标红——纯 RAG 做不到。
2. **Data Analyst 九段流水线**：数据画像 → RAG 召回绘图模板/统计规范 → 任务规划 → coder 生成代码 → Docker 沙箱执行 → 自动 Debug（≤3 轮）→ 两级结果校验 → 解释报告 → Notebook+环境锁打包。
3. **实验设计证据回溯**：Optuna 多目标优化，每条推荐方案必须引用 KG 中具体论文节点，无引用不出建议；实测结果回流形成先验闭环。
4. **领域包分区**：图谱/向量/先验按"领域包"隔离，跨包命中显式声明并降权，避免不同课题互相污染。

## 📁 项目结构

```
yanxitong/
├── src/
│   ├── agents/          # 7 个 Agent (supervisor/retriever/kg_builder/data_analyst/
│   │                    #   experiment_designer/writing_assistant/academic_reviewer)
│   ├── knowledge/       # GraphRAG 双引擎 (vector_store/graph_store/graphrag/kb)
│   ├── tools/           # 工具层 (arxiv/semantic_scholar/openalex/pdf_parser/sandbox)
│   ├── safety/          # 安全层 (hallucination/citation/triple_check/guard)
│   ├── workflows/       # LangGraph 工作流 (state/supervisor_graph)
│   ├── api/             # FastAPI 接口 (routes/schemas/auth/middleware)
│   ├── observability/   # 可观测性 (tracing/metrics/audit_store)
│   └── main.py          # 应用入口
├── tests/               # 单元 / 集成 / 评估
├── docs/                # 方案文档与 NEXT_PLAN
├── docker/              # Docker 配置
├── docker-compose.yml   # 一键部署
├── config.yaml          # 全局配置
└── README.md
```

## 📄 许可证

MIT License
