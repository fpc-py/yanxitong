# 研析通 v2.0

> **AI 科研合伙人** — 以课题组文献库与实验记录为知识底座，构建 "KG×RAG 双引擎 + 分层多智能体协作 + 全链路可观测" 的 AI 科研系统，覆盖 "文献调研 → 知识构建 → 数据挖掘 → 实验设计 → 论文合规" 五大环节。

## 🎯 一句话定位

研析通不是又一个 LLM 包装壳——我们用六道防线对抗幻觉、五层策略控制成本、Docker 沙箱保障安全执行、四层评估确保质量、全链路追踪实现可解释。这是一个真正可落地的、经过深度工程优化的 AI 科研合伙人系统。

## 🏗️ 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                  🎯 Supervisor Agent                      │
│  意图识别 · 任务分解 · 资源路由 · 冲突仲裁 · 质量门禁       │
└──────┬──────────┬──────────┬──────────┬─────────────────┘
       │          │          │          │
┌──────▼──┐ ┌─────▼───┐ ┌───▼────┐ ┌──▼──────────┐
│🕵️ Retriever│ │🧠 KG    │ │📊 Data │ │📝 Academic │
│  & Parser │ │ Builder │ │ Analyst│ │  Reviewer  │
└──────┬────┘ └────┬────┘ └───┬────┘ └─────┬───────┘
       │           │         │             │
┌──────▼───────────▼─────────▼─────────────▼──────────────┐
│              ⚙️ Tool & Resource Layer                    │
│  Arxiv API │ Semantic Scholar │ Python Sandbox │ Neo4j │
│  FAISS/BGE-M3 │ Redis Cache │ PDF Parser │ OTEL        │
└─────────────────────────────────────────────────────────┘
```

## 🚀 快速开始

```bash
# 1. 配置 API Key
echo "DASHSCOPE_API_KEY=your_key" > .env

# 2. 安装依赖
pip install -r requirements.txt

#PyYAML 6.0.1 已经为 Windows 提供了预编译的 wheel 包，无需本地编译，完全绕开 Cython 和策略问题。

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

- 账号 / 令牌 / 匿名配额在 MySQL，知识图谱在 Neo4j，语义缓存在 Redis（均容器卷持久化）。
- 会话状态（历史会话 / 引用链）、论文向量索引、上传文件落盘于 `data/`（compose 挂载 `app-data` 卷），后端重启后自动恢复。

## 🛡️ 六道幻觉防线

| 层 | 名称 | 说明 |
|---|------|------|
| 1 | 检索范围限定 | 声明必须能在源文献中找到对应内容 |
| 2 | 引用锚定 | 每个观点必须标注引用来源 |
| 3 | 知识图谱反验 | 声明与知识图谱事实交叉验证 |
| 4 | 自一致性检查 | 检测回复内部的逻辑矛盾 |
| 5 | 置信度评分 | 加权聚合评分，低于阈值标记 |
| 6 | 人工熔断 | 高风险结论触发人工审核 |

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

# 消融实验
python -m tests.evaluation.ablation

# 演示脚本
python -m tests.evaluation.demo_scenarios
```

## 🏆 竞赛创新亮点

### 工程创新 (6项)
1. GraphRAG 双引擎 (向量 + 知识图谱) — 每个结论可追溯到原文
2. 六道幻觉防线 — 系统性对抗 LLM 幻觉
3. Docker 安全沙箱 + 自动 Debug 闭环
4. 五层 Token 成本优化 — 模型路由/语义缓存/Prompt压缩/批处理/预算管控
5. 全链路可观测 — OpenTelemetry 追踪每个 Agent 调用
6. 分层评估 + CI 门禁 — RAGAS 自动化回归测试

### 产品创新 (3项)
7. 从被动问答到主动规划 — 指出文献矛盾、提出研究假设
8. 全周期闭环 — "找→读→算→写→审" 五环节打通
9. 科研经验可复用 — 课题组知识图谱持续积累

### 工程创新 (3项)
10. 熔断器 + 优雅降级 — 单点故障下仍可降级运行
11. 数据飞轮 — 用户纠错→微调样本→LoRA更新
12. Docker 安全沙箱 — 自托管方案，网络隔离+资源限制

## 📁 项目结构

```
yanxitong/
├── src/
│   ├── agents/          # 7个 Agent (supervisor/retriever/kg_builder/data_analyst/experiment_designer/writing_assistant/academic_reviewer)
│   ├── knowledge/       # GraphRAG 双引擎 (vector_store/graph_store/graphrag)
│   ├── tools/           # 工具层 (arxiv/semantic_scholar/pdf_parser/sandbox/citation_formatter)
│   ├── safety/          # 安全层 (hallucination/guard/citation)
│   ├── workflows/       # LangGraph 工作流 (state/supervisor_graph/subagent_graphs)
│   ├── api/             # FastAPI 接口 (routes/schemas/middleware)
│   ├── observability/   # 可观测性 (tracing/metrics)
│   └── main.py          # 应用入口
├── tests/
│   ├── unit/            # 单元测试
│   ├── integration/     # 集成测试 (Phase 1-6)
│   └── evaluation/      # 评估脚本 (RAGAS/消融/演示)
├── docker/              # Docker 配置
├── docker-compose.yml   # 一键部署
├── config.yaml          # 全局配置
└── README.md
```

## 📄 许可证

MIT License